"""Evidence cards, drawn on a saved simulator screenshot with no simulator running."""

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from healorhalt.evidence import (
    COLORS,
    GRAY,
    Evidence,
    Target,
    annotate,
    describe_change,
    locator_patch,
    render,
    save,
    scale_rect,
)
from healorhalt.fingerprint import fingerprint, parse
from healorhalt.locator import LocatorMap
from healorhalt.verdict import ESCALATE, HALT, HEAL, Check, Screen, Verdict

FIXTURES = Path(__file__).parent / "fixtures"
SHOT = (FIXTURES / "png" / "base_login.png").read_bytes()
"""A 1206 x 2622 pixel screenshot of the 402-point-wide login screen (3x)."""
WINDOW = 402
SUBMIT = (163, 512, 76, 35)
"""login.submit's frame in points, from base_login.xml."""


def submit_fingerprint():  # type: ignore[no-untyped-def]
    root = parse((FIXTURES / "xml" / "base_login.xml").read_text(encoding="utf-8"))
    (node,) = root.xpath('//*[@name="login.submit"]')
    return fingerprint("login.submit", node, root)


def test_frames_scale_from_points_to_pixels() -> None:
    assert scale_rect(SUBMIT, 1206, WINDOW) == (489, 1536, 717, 1641)
    assert scale_rect(SUBMIT, 402, WINDOW) == (163, 512, 239, 547)


def test_box_is_drawn_at_the_scaled_frame_on_a_real_screenshot() -> None:
    image = annotate(SHOT, SUBMIT, WINDOW, COLORS[HEAL])
    left, top, right, bottom = scale_rect(SUBMIT, image.width, WINDOW)
    assert image.size == (1206, 2622)
    assert image.getpixel((left + 1, (top + bottom) // 2)) == COLORS[HEAL]
    assert image.getpixel(((left + right) // 2, bottom - 1)) == COLORS[HEAL]
    # Outside the box the screenshot is untouched.
    original = Image.open(io.BytesIO(SHOT)).convert("RGB")
    assert image.getpixel((left - 20, top - 20)) == original.getpixel((left - 20, top - 20))


def test_tap_marker_is_a_crosshair_at_the_frame_centre() -> None:
    image = annotate(SHOT, SUBMIT, WINDOW, COLORS[HALT], action="tap")
    left, top, right, bottom = scale_rect(SUBMIT, image.width, WINDOW)
    cx, cy = (left + right) // 2, (top + bottom) // 2
    assert image.getpixel((cx, cy)) == COLORS[HALT]
    assert image.getpixel((cx + 20, cy)) == COLORS[HALT]


def test_old_position_is_dashed() -> None:
    image = annotate(SHOT, SUBMIT, WINDOW, GRAY, dashed=True)
    left, top, right, _ = scale_rect(SUBMIT, image.width, WINDOW)
    top_edge = [image.getpixel((x, top)) == GRAY for x in range(left, right)]
    assert any(top_edge) and not all(top_edge)


def test_typed_text_is_labelled_under_the_field() -> None:
    field = (44, 330, 314, 34)
    plain = annotate(SHOT, field, WINDOW, COLORS[HEAL])
    labelled = annotate(SHOT, field, WINDOW, COLORS[HEAL], action="type text into", typed="demo")
    _, _, _, bottom = scale_rect(field, plain.width, WINDOW)
    row = range(150, 400)
    below = bottom + 20
    assert any(labelled.getpixel((x, below)) == COLORS[HEAL] for x in row)
    assert not any(plain.getpixel((x, below)) == COLORS[HEAL] for x in row)


def test_description_says_what_changed() -> None:
    fp = submit_fingerprint()
    moved = Target("XCUIElementTypeButton", "", "Continue", (163, 554, 76, 35))
    assert describe_change(fp, moved) == (
        "Button 'Sign in' (login.submit) is now Button 'Continue' (no id), moved 42 pt down."
    )
    renamed = Target("XCUIElementTypeButton", "login.signIn", "Sign in", SUBMIT)
    assert describe_change(fp, renamed) == (
        "Button 'Sign in' (login.submit) is now Button 'Sign in' (login.signIn)."
    )
    assert (
        describe_change(fp, None) == "Button 'Sign in' (login.submit) wasn't found on this screen."
    )


def evidence() -> Evidence:
    return Evidence(
        key="login.submit",
        action="tap",
        window_width=WINDOW,
        fingerprint=submit_fingerprint(),
        target=Target("XCUIElementTypeButton", "login.passkey", "Sign in with Passkey", SUBMIT),
        before=SHOT,
        after=SHOT,
        result=SHOT,
        expected_screen=Screen("list", ("list.count",)),
        actual_screen=Screen("login", ("login.title",)),
        data={"tier": 1, "score": 0.75},
    )


def test_card_shows_three_panels_under_a_header_in_the_verdict_colour() -> None:
    verdict = Verdict(HALT, "expected the list screen; got the login screen", [])
    card = Image.open(io.BytesIO(render(evidence(), verdict))).convert("RGB")
    assert card.width == 16 + 3 * (360 + 16)
    assert card.getpixel((card.width // 2, 2)) == COLORS[HALT]
    assert card.height > 360 * 2622 / 1206  # the panels keep the screenshot's aspect ratio


def test_saved_evidence_has_the_card_screenshots_and_data(tmp_path: Path) -> None:
    verdict = Verdict(
        HALT, "expected the list screen; got the login screen", [Check("x", False, "")]
    )
    card = save(evidence(), verdict, tmp_path / "1-login.submit")
    files = sorted(p.name for p in card.parent.iterdir())
    assert files == ["after.png", "before.png", "card.png", "evidence.json", "result.png"]
    data = json.loads((card.parent / "evidence.json").read_text(encoding="utf-8"))
    assert data["verdict"] == "HALT" and data["tier"] == 1
    assert data["description"].endswith(
        "Likely real bug: expected the list screen; got the login screen."
    )
    assert data["expected_screen"] == {"name": "list", "ids": ["list.count"]}


LOCATORS = """\
# comment
login.submit: {id: login.submit, intent: submits the form}
list.row: item.row.{name}
add.save:
  predicate: "type == 'XCUIElementTypeButton' AND label == 'Save'"
  intent: saves the item

add.cancel: add.cancel
"""


def test_patch_points_the_locator_at_the_healed_element_and_keeps_its_intent() -> None:
    patch = locator_patch(LOCATORS, "locators.yaml", "add.save", "id", "add.confirm")
    assert patch is not None
    assert "-  predicate: \"type == 'XCUIElementTypeButton' AND label == 'Save'\"" in patch
    assert "+add.save: {id: add.confirm, intent: saves the item}" in patch
    assert "add.cancel" not in "".join(x for x in patch.splitlines() if x[:1] in "+-")


def test_patch_for_a_bare_id_stays_bare() -> None:
    patch = locator_patch("a: old\nb: other\n", "l.yaml", "a", "id", "new")
    assert patch is not None and "+a: new" in patch and "-a: old" in patch


@pytest.mark.parametrize("name", ["list.row", "missing"])
def test_no_patch_for_a_template_or_an_unknown_name(name: str) -> None:
    assert locator_patch(LOCATORS, "l.yaml", name, "id", "x") is None


def test_screens_come_from_screen_locators(tmp_path: Path) -> None:
    path = tmp_path / "locators.yaml"
    path.write_text("screen.login: login.title\nscreen.odd: {xpath: //x}\nlogin.title: t\n")
    assert LocatorMap.from_yaml(path).screens() == {"login": "login.title"}


def test_escalate_card_says_it_needs_review() -> None:
    verdict = Verdict(ESCALATE, "no green run recorded the screen after this step", [])
    card = Image.open(io.BytesIO(render(evidence(), verdict))).convert("RGB")
    assert card.getpixel((card.width // 2, 2)) == COLORS[ESCALATE]
