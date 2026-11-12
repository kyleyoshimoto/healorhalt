from pathlib import Path

import pytest
from appium.webdriver.common.appiumby import AppiumBy

from healorhalt.locator import L, LocatorError, LocatorMap, use_locators


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "locators.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_bare_string_is_accessibility_id(tmp_path: Path) -> None:
    locators = LocatorMap.from_yaml(write(tmp_path, "login.submit: login.submit\n"))
    loc = locators.get("login.submit")
    assert (loc.strategy, loc.value, loc.by) == ("id", "login.submit", AppiumBy.ACCESSIBILITY_ID)


def test_explicit_strategy(tmp_path: Path) -> None:
    locators = LocatorMap.from_yaml(
        write(tmp_path, "list.rows: {predicate: \"name BEGINSWITH 'item.row.'\"}\n")
    )
    loc = locators.get("list.rows")
    assert loc.by == AppiumBy.IOS_PREDICATE
    assert loc.value == "name BEGINSWITH 'item.row.'"


def test_template_parameters(tmp_path: Path) -> None:
    locators = LocatorMap.from_yaml(write(tmp_path, "item.row: item.row.{name}\n"))
    assert locators.get("item.row", name="Eggs").value == "item.row.Eggs"


def test_missing_parameter(tmp_path: Path) -> None:
    locators = LocatorMap.from_yaml(write(tmp_path, "item.row: item.row.{name}\n"))
    with pytest.raises(LocatorError, match="needs parameter"):
        locators.get("item.row")


def test_unknown_name(tmp_path: Path) -> None:
    locators = LocatorMap.from_yaml(write(tmp_path, "a: a\n"))
    with pytest.raises(LocatorError, match="unknown locator 'b'"):
        locators.get("b")


@pytest.mark.parametrize(
    "text",
    ["a: {css: '.x'}\n", "a: {id: x, xpath: y}\n", "a: [x]\n", "- a\n"],
)
def test_malformed_files(tmp_path: Path, text: str) -> None:
    with pytest.raises(LocatorError):
        LocatorMap.from_yaml(write(tmp_path, text))


def test_L_uses_active_map(tmp_path: Path) -> None:
    use_locators(LocatorMap.from_yaml(write(tmp_path, "login.submit: login.submit\n")))
    try:
        assert L("login.submit").value == "login.submit"
    finally:
        use_locators(None)
    with pytest.raises(LocatorError, match="no locator file"):
        L("login.submit")


def test_demo_locator_file_parses() -> None:
    path = Path(__file__).parents[1] / "demo" / "locators.yaml"
    locators = LocatorMap.from_yaml(path)
    assert "login.submit" in locators


def test_intent_is_kept_but_not_part_of_identity(tmp_path: Path) -> None:
    locators = LocatorMap.from_yaml(
        write(tmp_path, "login.submit: {id: login.submit, intent: signs in}\nplain: plain\n")
    )
    loc = locators.get("login.submit")
    assert (loc.strategy, loc.value, loc.intent) == ("id", "login.submit", "signs in")
    assert loc.replaced("id", "login.signIn").intent == "signs in"
    assert loc.key == "login.submit"
    assert locators.get("plain").intent == ""


def test_intent_needs_exactly_one_strategy(tmp_path: Path) -> None:
    with pytest.raises(LocatorError, match="exactly one"):
        LocatorMap.from_yaml(write(tmp_path, "a: {intent: does a thing}\n"))
