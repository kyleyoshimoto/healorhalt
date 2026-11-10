import io
from pathlib import Path

import pytest
from PIL import Image

from healorhalt.fingerprint import Fingerprint, crop, fingerprint, parse
from healorhalt.heal import Healer
from healorhalt.locator import Locator
from healorhalt.store import HealRecord, Store

FIXTURES = Path(__file__).parent / "fixtures" / "xml"
APP = "com.example.app"
SUBMIT = Locator("login.submit", "id", "login.submit")


def xml(name: str) -> str:
    return (FIXTURES / f"{name}.xml").read_text(encoding="utf-8")


def submit_fingerprint() -> Fingerprint:
    root = parse(xml("base_login"))
    (node,) = root.xpath('//XCUIElementTypeButton[@name="login.submit"]')
    return fingerprint("login.submit", node, root)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "db" / "healorhalt.db")


def healer(store: Store, mode: str = "heuristic") -> Healer:
    return Healer(store, APP, mode, run_id="run1", test="tests/x.py::test_y", mutation="M01")


def test_fingerprint_round_trips_through_json() -> None:
    fp = submit_fingerprint()
    assert Fingerprint.from_json(fp.to_json()) == fp
    assert fp.label == "Sign in"
    assert "Pantry" in fp.neighbors


def test_locator_key_includes_params() -> None:
    row = Locator("list.row", "id", "item.row.Eggs", (("name", "Eggs"),))
    assert row.key == "list.row[name=Eggs]"
    assert row.replaced("id", "pantry.Eggs").key == row.key


def test_store_saves_and_loads_fingerprints(store: Store) -> None:
    fp = submit_fingerprint()
    store.save_fingerprint(APP, fp, b"png", "t")
    assert store.fingerprint(APP, "login.submit") == fp
    assert store.fingerprint("other.app", "login.submit") is None
    assert store.fingerprint_count(APP) == 1


def test_approve_requires_a_successful_heal(store: Store) -> None:
    failed = store.log_heal(HealRecord("r", APP, "t", None, "k", 1, "below_threshold", "id", "old"))
    with pytest.raises(LookupError):
        store.approve(failed)
    healed = store.log_heal(
        HealRecord("r", APP, "t", None, "k", 1, "healed", "id", "old", "id", "new", 0.9)
    )
    store.approve(healed)
    assert store.cached(APP, "k") == ("id", "new")


def test_tier1_heals_and_logs_the_score(store: Store) -> None:
    store.save_fingerprint(APP, submit_fingerprint(), b"", "t")
    tried: list[Locator] = []

    def try_find(locator: Locator) -> object:
        tried.append(locator)
        return "element"

    result = healer(store).heal(SUBMIT, xml("M01_login"), try_find)  # type: ignore[arg-type]
    assert result == "element"
    assert tried == [SUBMIT.replaced("id", "login.signIn")]
    (row,) = store.heals(run_id="run1")
    assert (row["outcome"], row["tier"], row["mutation"]) == ("healed", 1, "M01")
    assert row["score"] > 0.8


def test_missing_fingerprint_is_logged_not_guessed(store: Store) -> None:
    assert healer(store).heal(SUBMIT, xml("M01_login"), lambda _: None) is None
    (row,) = store.heals(run_id="run1")
    assert row["outcome"] == "no_fingerprint"


def test_below_threshold_is_not_healed(store: Store) -> None:
    store.save_fingerprint(APP, submit_fingerprint(), b"", "t")
    strict = Healer(store, APP, "heuristic", run_id="run1", test="t", threshold=0.99)
    assert strict.heal(SUBMIT, xml("M01_login"), lambda _: "element") is None  # type: ignore[arg-type,return-value]
    (row,) = store.heals(run_id="run1")
    assert row["outcome"] == "below_threshold"


def test_approved_heal_is_used_before_scoring(store: Store) -> None:
    healed = store.log_heal(
        HealRecord("r", APP, "t", None, "login.submit", 1, "healed", "id", "x", "id", "cached.id")
    )
    store.approve(healed)
    tried: list[Locator] = []

    def try_find(locator: Locator) -> object:
        tried.append(locator)
        return "element"

    assert healer(store, mode="cache").heal(SUBMIT, xml("M01_login"), try_find) == "element"  # type: ignore[arg-type]
    assert tried == [SUBMIT.replaced("id", "cached.id")]
    assert store.heals(run_id="run1")[0]["tier"] == 0


def test_crop_scales_points_to_pixels(tmp_path: Path) -> None:
    image = Image.new("RGB", (300, 600))
    path = tmp_path / "shot.png"
    image.save(path)
    piece = crop(path.read_bytes(), (10, 20, 30, 40), window_width=100)
    assert Image.open(io.BytesIO(piece)).size == (90, 120)
