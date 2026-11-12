"""Tier 2 against recorded model answers, so CI needs no Ollama.

Answers in fixtures/llm/ were recorded from qwen3-vl:8b by scripts/eval_tier2.py --record. Each
names a box by number, so these tests also pin down that pruning numbers the same elements the
same way every time: a recorded number must still point at the element the model meant.
"""

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from healorhalt.fingerprint import Fingerprint, Node, fingerprint, parse
from healorhalt.heal import Healer
from healorhalt.heal.tier2_llm import (
    BOX_COLOR,
    IMAGE_WIDTH,
    SKIP_THINKING,
    HealProposal,
    chosen,
    draw_marks,
    propose,
)
from healorhalt.locator import Locator
from healorhalt.prune import TOKEN_BUDGET, prune
from healorhalt.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = sorted((FIXTURES / "llm").glob("*.json"))
# Recorded answers where the model picked a look-alike for an element that isn't on screen.
# Tier 2 alone can't be trusted with these; the Phase 4 verdict engine has to catch them.
KNOWN_WRONG = {"N01", "N03"}


def xml(name: str) -> Node:
    return parse((FIXTURES / "xml" / f"{name}.xml").read_text(encoding="utf-8"))


def fingerprint_of(screen: str, name: str, key: str) -> Fingerprint:
    root = xml(screen)
    (node,) = root.xpath(
        "//*[@name=$n][@accessible='true' or @type!='XCUIElementTypeOther']", n=name
    )
    return fingerprint(key, node, root)


class FakeClient:
    """Stands in for ollama.Client: returns a canned answer, or raises."""

    def __init__(self, content: str = "", error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def chat(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            message=SimpleNamespace(content=self.content), prompt_eval_count=1500, eval_count=40
        )


def answer(**fields: Any) -> str:
    base = {"element_still_exists": True, "candidate_id": 1, "confidence": 0.9, "rationale": "r"}
    return json.dumps(base | fields)


@pytest.mark.parametrize("path", RECORDED, ids=[p.stem for p in RECORDED])
def test_recorded_answer_names_the_expected_element(path: Path) -> None:
    case = json.loads(path.read_text(encoding="utf-8"))
    if case["case"] in KNOWN_WRONG:
        pytest.xfail("model picks a look-alike for a missing element; Phase 4 must catch it")
    fp = fingerprint_of(case["fingerprint_from"], case["before"], case["locator"])
    root = xml(case["page_source"])
    screen, call = propose(fp, root, None, FakeClient(case["response"]), case["locator"])
    assert call.proposal is not None, call.error
    mark = chosen(screen, call.proposal)
    if case["expected"] is None:
        assert mark is None
        return
    assert mark is not None
    names = [mark.node.get("name")] + [a.get("name") for a in mark.node.iterancestors()][:2]
    assert case["expected"] in names


@pytest.mark.parametrize("path", RECORDED, ids=[p.stem for p in RECORDED])
def test_pruned_screen_fits_the_token_budget(path: Path) -> None:
    case = json.loads(path.read_text(encoding="utf-8"))
    fp = fingerprint_of(case["fingerprint_from"], case["before"], case["locator"])
    screen = prune(fp, xml(case["page_source"]))
    assert screen.tokens <= TOKEN_BUDGET
    assert 0 < len(screen.marks) <= 15


def test_marks_are_numbered_in_reading_order() -> None:
    fp = fingerprint_of("base_list", "list.settings", "list.settings")
    screen = prune(fp, xml("M14_list"))
    positions = [(m.rect[1], m.rect[0]) for m in screen.marks]
    assert positions == sorted(positions)
    assert [m.id for m in screen.marks] == list(range(1, len(screen.marks) + 1))


def test_request_carries_intent_image_schema_and_skips_thinking() -> None:
    fp = fingerprint_of("base_detail", "detail.edit", "detail.edit")
    client = FakeClient(answer())
    png = _png(1206, 2622)
    propose(fp, xml("M13_detail"), png, client, "detail.edit", "edits the item", "tap")
    (request,) = client.calls
    user, prefill = request["messages"]
    assert "tap the element that edits the item" in user["content"]
    assert "'detail.edit'" in user["content"]
    assert len(user["images"]) == 1
    assert prefill == SKIP_THINKING
    assert request["format"] == HealProposal.model_json_schema()


def test_percent_confidence_is_read_as_a_fraction() -> None:
    assert HealProposal.model_validate_json(answer(confidence=95)).confidence == 0.95
    assert HealProposal.model_validate_json(answer(confidence=1)).confidence == 1.0


@pytest.mark.parametrize(
    "content",
    ["not json", answer(confidence=250), '{"candidate_id": 1}', answer(candidate_id="three")],
)
def test_malformed_answer_is_no_proposal(content: str) -> None:
    fp = fingerprint_of("base_list", "list.settings", "list.settings")
    _, call = propose(fp, xml("M10_list"), None, FakeClient(content), "list.settings")
    assert call.proposal is None
    assert call.error is not None and call.error.startswith("invalid answer")
    assert call.response == content


def test_timeout_is_no_proposal() -> None:
    fp = fingerprint_of("base_list", "list.settings", "list.settings")
    client = FakeClient(error=TimeoutError("timed out"))
    _, call = propose(fp, xml("M10_list"), None, client, "list.settings")
    assert call.proposal is None
    assert call.error == "TimeoutError: timed out"
    assert call.response is None


def test_number_that_wasnt_drawn_names_nothing() -> None:
    fp = fingerprint_of("base_list", "list.settings", "list.settings")
    screen, call = propose(
        fp, xml("M10_list"), None, FakeClient(answer(candidate_id=99)), "list.settings"
    )
    assert call.proposal is not None
    assert chosen(screen, call.proposal) is None


def _png(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, format="PNG")
    return out.getvalue()


def test_marks_are_drawn_at_scaled_positions() -> None:
    fp = fingerprint_of("base_list", "list.settings", "list.settings")
    screen = prune(fp, xml("M10_list"))
    # A 3x screenshot of the 402-point-wide window, shrunk to IMAGE_WIDTH pixels.
    image = Image.open(io.BytesIO(draw_marks(_png(1206, 2622), screen.marks, 402)))
    assert image.width == IMAGE_WIDTH
    assert image.height == round(2622 * IMAGE_WIDTH / 1206)
    scale = IMAGE_WIDTH / 402
    mark = next(m for m in screen.marks if m.node.get("name") == "list.preferences")
    x, y, w, h = mark.rect
    # The box's bottom-right corner is outlined, and its middle is left alone.
    assert image.getpixel((round((x + w) * scale), round((y + h) * scale))) == BOX_COLOR
    assert image.getpixel((round((x + w / 2) * scale), round((y + h / 2) * scale))) == (
        255,
        255,
        255,
    )


# Healer: Tier 2 runs only in llm mode, and only after Tier 1 finds nothing good enough.

EDIT = Locator("detail.edit", "id", "detail.edit", intent="switches into editing mode")


@pytest.fixture
def store(tmp_path: Path) -> Store:
    db = Store(tmp_path / "healorhalt.db")
    db.save_fingerprint(APP, fingerprint_of("base_detail", "detail.edit", "detail.edit"), b"", "t")
    return db


APP = "com.example.app"


def _healer(store: Store, mode: str, client: FakeClient) -> Healer:
    return Healer(store, APP, mode, run_id="r", test="t", llm_client=client, llm_model="m")


def _change_mark_id() -> int:
    fp = fingerprint_of("base_detail", "detail.edit", "detail.edit")
    screen = prune(fp, xml("M13_detail"))
    return next(m.id for m in screen.marks if m.node.get("label") == "Change")


def _source(name: str) -> str:
    return (FIXTURES / "xml" / f"{name}.xml").read_text(encoding="utf-8")


def test_llm_mode_heals_what_tier1_misses(store: Store) -> None:
    client = FakeClient(answer(candidate_id=_change_mark_id(), confidence=0.97))
    found: list[Locator] = []

    def try_find(locator: Locator) -> object:
        found.append(locator)
        return "element"

    element = _healer(store, "llm", client).heal(
        EDIT, _source("M13_detail"), try_find, lambda: _png(1206, 2622), "tap"
    )
    assert element == "element"
    tier1, tier2 = store.heals(run_id="r")
    assert (tier1["tier"], tier1["outcome"]) == (1, "below_threshold")
    assert (tier2["tier"], tier2["outcome"], tier2["score"]) == (2, "healed", 0.97)
    assert tier2["latency_s"] is not None
    assert found[-1].value == "type == 'XCUIElementTypeButton' AND label == 'Change'"
    call = store.llm_call(tier2["id"])
    assert call is not None and call["model"] == "m" and call["prompt_tokens"] == 1500


def test_heuristic_mode_never_asks_the_llm(store: Store) -> None:
    client = FakeClient(answer())
    healer = _healer(store, "heuristic", client)
    assert healer.heal(EDIT, _source("M13_detail"), lambda _: "element") is None
    assert client.calls == []


@pytest.mark.parametrize(
    ("client", "outcome"),
    [
        (FakeClient(answer(element_still_exists=False, candidate_id=None)), "no_proposal"),
        (FakeClient(answer(confidence=0.3)), "low_confidence"),
        (FakeClient(error=ConnectionError("refused")), "llm_error"),
    ],
    ids=["gone", "unsure", "unreachable"],
)
def test_llm_mode_logs_why_it_did_not_heal(store: Store, client: FakeClient, outcome: str) -> None:
    healer = _healer(store, "llm", client)
    assert healer.heal(EDIT, _source("M13_detail"), lambda _: "element") is None
    tier2 = store.heals(run_id="r")[-1]
    assert (tier2["tier"], tier2["outcome"]) == (2, outcome)
    assert store.llm_call(tier2["id"]) is not None
