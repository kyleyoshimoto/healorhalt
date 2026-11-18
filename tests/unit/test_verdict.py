"""The verdict engine's checks and decisions, on saved page sources and recorded LLM answers."""

import json
from pathlib import Path
from typing import Any

import pytest

from healorhalt.fingerprint import Fingerprint, fingerprint, parse
from healorhalt.heal import Healer
from healorhalt.heal.tier2_llm import HealProposal, chosen
from healorhalt.locator import Locator
from healorhalt.prune import prune
from healorhalt.store import HealRecord, Store
from healorhalt.verdict import (
    ESCALATE,
    HALT,
    HEAL,
    Check,
    Screen,
    VerdictConfig,
    check_effect,
    check_replay,
    check_score,
    check_screen,
    check_type,
    combined_score,
    decide,
    screen_of,
)

FIXTURES = Path(__file__).parent / "fixtures"
APP = "com.example.app"
CONFIG = VerdictConfig()
MARKERS = {
    "login": "login.title",
    "list": "list.count",
    "add": "add.name",
    "detail": "detail.name",
    "settings": "settings.sortAlphabetically",
}

DELETE_ALERT = Screen(None, ("detail.confirmDelete",))
"""What a green run of test_delete_item reached after tapping Delete Item: an alert covering the
detail screen, recorded by `make record`."""


def source(name: str) -> str:
    return (FIXTURES / "xml" / f"{name}.xml").read_text(encoding="utf-8")


def fingerprint_of(screen: str, name: str, key: str) -> Fingerprint:
    root = parse(source(screen))
    (node,) = root.xpath(
        "//*[@name=$n][@accessible='true' or @type!='XCUIElementTypeOther']", n=name
    )
    return fingerprint(key, node, root)


def screen(name: str) -> Screen:
    return screen_of(parse(source(name)), MARKERS)


# decide()


def test_every_check_passing_is_heal() -> None:
    verdict = decide([Check("a", True, "ok"), Check("b", True, "ok")])
    assert verdict.decision == HEAL


def test_failed_soft_check_or_unrun_check_is_escalate() -> None:
    assert decide([Check("a", True, ""), Check("b", False, "close", hard=False)]).decision == (
        ESCALATE
    )
    unrun = decide([Check("a", True, ""), Check("b", None, "not recorded", hard=False)])
    assert (unrun.decision, unrun.reason) == (ESCALATE, "not recorded")


def test_failed_hard_check_is_halt_whatever_else_passed() -> None:
    verdict = decide(
        [
            Check("a", False, "soft", hard=False),
            Check("b", False, "wrong screen"),
            Check("c", True, ""),
        ]
    )
    assert (verdict.decision, verdict.reason) == (HALT, "wrong screen")


# Round 1


def test_llm_confidence_alone_cant_reach_heal() -> None:
    sure_llm_no_support = combined_score(2, 0.0, 1.0, CONFIG)
    assert sure_llm_no_support == 0.5
    assert check_score(sure_llm_no_support, CONFIG).passed is False
    assert check_score(sure_llm_no_support, CONFIG).hard is False  # ESCALATE, not HEAL
    assert combined_score(0, None, None, CONFIG) == 1.0
    assert combined_score(1, 0.7, None, CONFIG) == 0.7


@pytest.mark.parametrize(
    ("score", "passed", "hard"), [(0.75, True, True), (0.5, False, False), (0.3, False, True)]
)
def test_score_bands(score: float, passed: bool, hard: bool) -> None:
    check = check_score(score, CONFIG)
    assert (check.passed, check.hard) == (passed, hard)


def test_type_change_among_tappables_escalates_and_otherwise_halts() -> None:
    fp = fingerprint_of("base_settings", "settings.logout", "settings.logout")
    root = parse(source("base_settings"))
    (cell,) = root.xpath("(//XCUIElementTypeCell)[1]")
    (switch,) = root.xpath("(//XCUIElementTypeSwitch)[1]")
    (button,) = root.xpath('//*[@name="settings.logout"]')
    assert check_type(fp, button).passed is True
    assert (check_type(fp, cell).passed, check_type(fp, cell).hard) == (False, False)
    assert (check_type(fp, switch).passed, check_type(fp, switch).hard) == (False, True)


# Round 2


def test_typed_text_must_show_up() -> None:
    assert check_effect("type text into", "XCUIElementTypeTextField", "E", "E").passed
    ignored = check_effect("type text into", "XCUIElementTypeTextField", "E", "Search items")
    assert (ignored.passed, ignored.hard) == (False, True)


def test_secure_field_shows_one_dot_per_character() -> None:
    field = "XCUIElementTypeSecureTextField"
    assert check_effect("type text into", field, "pantry123", "•" * 9).passed
    assert not check_effect("type text into", field, "pantry123", "Password").passed


def test_toggle_must_change_value() -> None:
    assert check_effect("toggle", "XCUIElementTypeSwitch", "1", "0").passed
    assert not check_effect("toggle", "XCUIElementTypeSwitch", "1", "1").passed
    assert check_effect("tap", "XCUIElementTypeButton", None, None).passed


def test_screens_are_named_by_their_markers() -> None:
    assert screen("base_list").name == "list"
    assert screen("base_settings").name == "settings"
    assert "list.add" in screen("base_list").ids
    assert not any(i.startswith("Eggs") for i in screen("base_list").ids)  # labels aren't ids


def test_moved_and_renamed_elements_still_look_like_the_same_screen() -> None:
    for mutated in ("M03_list", "M07_list", "M10_list", "M14_list"):
        assert check_screen(screen("base_list"), screen(mutated), CONFIG).passed, mutated


def test_wrong_screen_halts() -> None:
    check = check_screen(screen("base_settings"), screen("base_add"), CONFIG)
    assert (check.passed, check.hard) == (False, True)
    assert "expected the settings screen" in check.detail and "got the add screen" in check.detail


def test_unrecorded_screen_escalates() -> None:
    check = check_screen(None, screen("base_list"), CONFIG)
    assert (check.passed, check.hard) == (None, False)


def test_keyboard_doesnt_count_as_part_of_the_screen() -> None:
    xml = """<AppiumAUT><XCUIElementTypeApplication name="App" label="App" visible="true">
      <XCUIElementTypeTextField name="login.username" label="" visible="true"/>
      <XCUIElementTypeKeyboard name="kb" visible="true">
        <XCUIElementTypeKey name="shift" label="Shift" visible="true"/>
      </XCUIElementTypeKeyboard>
      <XCUIElementTypeOther name="SystemInputAssistantView" visible="true">
        <XCUIElementTypeButton name="kb-autofill-key" label="Passwords" visible="true"/>
      </XCUIElementTypeOther>
    </XCUIElementTypeApplication></AppiumAUT>"""
    assert screen_of(parse(xml), {}).ids == ("login.username",)


def test_screen_round_trips_through_a_dict() -> None:
    s = screen("base_list")
    assert Screen.from_dict(json.loads(json.dumps(s.to_dict()))) == s


# Round 3


def test_a_failing_test_after_a_heal_halts_it() -> None:
    check = check_replay(False, "E   AssertionError: assert 'Eggs, 13' == 'Eggs, 12'\n")
    assert (check.passed, check.hard) == (False, True)
    assert "assert 'Eggs, 13' == 'Eggs, 12'" in check.detail
    assert check_replay(True).passed


# The healer in full mode


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "healorhalt.db")


def full(store: Store, client: Any = None, config: VerdictConfig = CONFIG) -> Healer:
    return Healer(store, APP, "full", "r", "t", llm_client=client, llm_model="m", verdict=config)


def test_full_mode_runs_round_one_on_a_tier1_heal(store: Store) -> None:
    store.save_fingerprint(
        APP, fingerprint_of("base_login", "login.submit", "login.submit"), b"", "t"
    )
    submit = Locator("login.submit", "id", "login.submit")
    heal = full(store).heal(submit, source("M01_login"), lambda _: "el", count=lambda _: 1)  # type: ignore[arg-type,return-value]
    assert heal is not None
    assert [c.name for c in heal.checks] == ["unique", "type", "score", "ambiguity"]
    assert decide(heal.checks).decision == HEAL


def test_two_matches_for_the_healed_locator_halt(store: Store) -> None:
    store.save_fingerprint(
        APP, fingerprint_of("base_login", "login.submit", "login.submit"), b"", "t"
    )
    submit = Locator("login.submit", "id", "login.submit")
    heal = full(store).heal(submit, source("M01_login"), lambda _: "el", count=lambda _: 2)  # type: ignore[arg-type,return-value]
    assert heal is not None and decide(heal.checks).decision == HALT


def test_close_runner_up_escalates(store: Store) -> None:
    store.save_fingerprint(APP, fingerprint_of("base_add", "add.save", "add.save"), b"", "t")
    save = Locator("add.save", "predicate", "label == 'Save'")
    wide = VerdictConfig(ambiguity_margin=0.5)  # M02's runner-up, Cancel, is 0.22 behind
    heal = full(store, config=wide).heal(save, source("M02_add"), lambda _: "el", count=lambda _: 1)  # type: ignore[arg-type,return-value]
    assert heal is not None and decide(heal.checks).decision == ESCALATE


class FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content

    def chat(self, **_: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(message=SimpleNamespace(content=self.content))


def _recorded(case: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "llm" / f"{case}.json").read_text("utf-8"))
    return data


def test_llm_saying_gone_finds_nothing_and_says_why(store: Store) -> None:
    case = _recorded("N02")
    fp = fingerprint_of(case["fingerprint_from"], case["before"], case["locator"])
    store.save_fingerprint(APP, fp, b"", "t")
    healer = full(store, FakeClient(case["response"]))
    locator = Locator(case["locator"], "id", "gone")
    assert healer.heal(locator, source(case["page_source"]), lambda _: "el") is None  # type: ignore[arg-type,return-value]
    assert healer.miss is not None and healer.miss.reason == "the LLM says the element is gone"


def test_low_llm_confidence_is_left_to_the_verdict_in_full_mode(store: Store) -> None:
    fp = fingerprint_of("base_detail", "detail.edit", "detail.edit")
    store.save_fingerprint(APP, fp, b"", "t")
    root = parse(source("M13_detail"))
    change = next(m.id for m in prune(fp, root).marks if m.node.get("label") == "Change")
    answer = json.dumps(
        {"element_still_exists": True, "candidate_id": change, "confidence": 0.3, "rationale": ""}
    )
    edit = Locator("detail.edit", "id", "detail.edit")
    heal = full(store, FakeClient(answer)).heal(edit, source("M13_detail"), lambda _: "el")  # type: ignore[arg-type,return-value]
    assert heal is not None and heal.tier == 2
    assert decide(heal.checks).decision == ESCALATE  # unique couldn't be counted; score is low


@pytest.mark.parametrize(
    ("case", "expected_after", "reached"),
    [("N01", "", "base_detail"), ("N03", "base_list", "base_add")],
)
def test_look_alike_picks_are_caught_by_the_next_screen(
    case: str, expected_after: str, reached: str
) -> None:
    """The recorded look-alike picks clear round 1, so round 2 has to catch them.

    N01: Delete Item is missing from the list screen and the LLM picks the Eggs row, which opens
    Eggs' detail screen; the recorded run reached the delete alert. N03: Save is missing and the
    LLM picks Add item, which opens the add form; the recorded run went back to the list.
    """
    data = _recorded(case)
    fp = fingerprint_of(data["fingerprint_from"], data["before"], data["locator"])
    root = parse(source(data["page_source"]))
    screen_now = prune(fp, root)
    proposal = HealProposal.model_validate_json(data["response"])
    assert chosen(screen_now, proposal) is not None  # the LLM did pick something
    expected = DELETE_ALERT if case == "N01" else screen(expected_after)
    check = check_screen(expected, screen(reached), CONFIG)
    assert check.passed is False and check.hard


# Store


def test_old_database_gains_the_verdict_columns(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE heals (id INTEGER PRIMARY KEY, created_at TEXT, run_id TEXT, app TEXT,"
        " test TEXT, mutation TEXT, key TEXT, tier INTEGER, outcome TEXT, old_strategy TEXT,"
        " old_value TEXT, new_strategy TEXT, new_value TEXT, score REAL, candidates TEXT)"
    )
    db.commit()
    db.close()
    store = Store(path)
    heal_id = store.log_heal(
        HealRecord("r", APP, "t", None, "k", 1, "healed", "id", "a", "id", "b")
    )
    store.set_verdict(heal_id, decide([Check("x", False, "bad")]), "card.png")
    row = store.heal(heal_id)
    assert row is not None and (row["verdict"], row["reason"], row["evidence"]) == (
        "HALT",
        "bad",
        "card.png",
    )


def test_halted_heal_cant_be_approved(store: Store) -> None:
    heal_id = store.log_heal(
        HealRecord("r", APP, "t", None, "k", 1, "healed", "id", "a", "id", "b")
    )
    store.set_verdict(heal_id, decide([Check("x", False, "bad")]))
    with pytest.raises(LookupError, match="halted"):
        store.approve(heal_id)


def test_screens_are_saved_per_test_and_step(store: Store) -> None:
    store.save_screen(APP, "t1", "login.submit|tap|1", screen("base_list"))
    assert store.screen(APP, "t1", "login.submit|tap|1") == screen("base_list")
    assert store.screen(APP, "t2", "login.submit|tap|1") is None
