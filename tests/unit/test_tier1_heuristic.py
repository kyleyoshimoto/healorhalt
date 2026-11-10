"""Tier 1 ranking against page sources saved from the demo app (scripts/capture_fixtures.py)."""

from pathlib import Path

import pytest

from healorhalt.fingerprint import Node, fingerprint, parse
from healorhalt.heal import DEFAULT_THRESHOLD, locator_for
from healorhalt.heal.tier1_heuristic import rank

FIXTURES = Path(__file__).parent / "fixtures" / "xml"


def load(name: str) -> Node:
    return parse((FIXTURES / f"{name}.xml").read_text(encoding="utf-8"))


def one(root: Node, xpath: str) -> Node:
    (node,) = root.xpath(xpath)
    return node


# (mutation, screen, element on the unmutated screen, the same element after the mutation)
CASES = [
    ("M01", "login", '//XCUIElementTypeButton[@name="login.submit"]',
     '//XCUIElementTypeButton[@name="login.signIn"]'),
    ("M02", "add", '//XCUIElementTypeButton[@name="add.save"]',
     '//XCUIElementTypeButton[@name="add.save"]'),
    ("M03", "list", '//XCUIElementTypeButton[@name="list.add"]',
     '//XCUIElementTypeButton[@name="list.add"]'),
    ("M04", "detail", '//XCUIElementTypeButton[@name="detail.delete"]',
     '//XCUIElementTypeButton[@name="detail.delete"]'),
    ("M05", "detail", '//XCUIElementTypeButton[@name="detail.edit"]',
     '//XCUIElementTypeButton[@name="detail.modify"]'),
    ("M06", "settings", '//XCUIElementTypeButton[@name="settings.logout"]',
     '//XCUIElementTypeButton[@name="settings.logout"]'),
    ("M07", "list", '//XCUIElementTypeTextField[@name="list.search"]',
     '//XCUIElementTypeTextField[@name="list.search"]'),
    ("M08", "login", '//XCUIElementTypeTextField[@name="login.username"]',
     '//XCUIElementTypeTextField[@name="login.username"]'),
    ("M09", "settings", '//XCUIElementTypeSwitch[@name="settings.showQuantities"]',
     '//XCUIElementTypeSwitch[@name="settings.quantities"]'),
    ("M10", "list", '//XCUIElementTypeButton[@name="list.settings"]',
     '//XCUIElementTypeButton[@name="list.preferences"]'),
]  # fmt: skip


@pytest.mark.parametrize(
    ("mutation", "screen", "before", "after"), CASES, ids=[c[0] for c in CASES]
)
def test_top_candidate_is_the_moved_element(
    mutation: str, screen: str, before: str, after: str
) -> None:
    base = load(f"base_{screen}")
    fp = fingerprint("target", one(base, before), base)
    mutated = load(f"{mutation}_{screen}")
    ranked = rank(fp, mutated)
    assert ranked[0].node is one(mutated, after), [c.describe() for c in ranked[:3]]
    assert ranked[0].score >= DEFAULT_THRESHOLD


def test_unchanged_screen_scores_the_element_itself_highest() -> None:
    base = load("base_list")
    node = one(base, '//XCUIElementTypeButton[@name="item.row.Eggs"]')
    ranked = rank(fingerprint("row", node, base), base)
    assert ranked[0].node is node
    assert ranked[0].score == pytest.approx(1.0)


def test_locator_for_prefers_unique_id() -> None:
    root = load("M01_login")
    node = one(root, '//XCUIElementTypeButton[@name="login.signIn"]')
    assert locator_for(node, root) == ("id", "login.signIn")


def test_locator_for_falls_back_to_label_when_id_is_just_the_label() -> None:
    root = load("base_detail")
    (cancel,) = [n for n in root.iter("XCUIElementTypeStaticText") if n.get("name") == "Name"]
    assert locator_for(cancel, root) == (
        "predicate",
        "type == 'XCUIElementTypeStaticText' AND label == 'Name'",
    )
