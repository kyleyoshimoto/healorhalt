"""Evidence cards: annotated screenshots and a plain-English description of every verdict.

A card puts three screenshots side by side: the screen on the last green run with the original
element boxed in dashed gray (before), the screen now with the healed element boxed in the
verdict's colour (after), and the screen the action led to (result). Each is saved with the
card's data as JSON, so any verdict can be audited later.

Element frames are in points and screenshots in pixels, so every frame is scaled by
``screenshot width / window width`` (3x on most iPhones) before drawing.
"""

import difflib
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageDraw, ImageFont

from healorhalt.fingerprint import Fingerprint, Rect, center
from healorhalt.verdict import ESCALATE, HALT, HEAL, Screen, Verdict, short

COLORS = {HEAL: (22, 163, 74), ESCALATE: (217, 119, 6), HALT: (220, 38, 38)}
GRAY = (128, 128, 128)
PANEL_WIDTH = 360
MARGIN = 16
HEADER = 96
CAPTION = 44
BACKGROUND = (255, 255, 255)
TEXT = (20, 20, 20)


@dataclass(frozen=True)
class Target:
    """The element a heal chose, as it is now."""

    type: str
    name: str
    label: str
    rect: Rect


@dataclass
class Evidence:
    """Everything a card shows, gathered while the step runs."""

    key: str
    action: str
    window_width: int
    fingerprint: Fingerprint | None = None
    target: Target | None = None
    before: bytes | None = None
    """The screen on the last green run, saved with the fingerprint."""
    after: bytes | None = None
    """The screen now, before the action."""
    result: bytes | None = None
    """The screen after the action."""
    typed: str | None = None
    expected_screen: Screen | None = None
    actual_screen: Screen | None = None
    data: dict[str, Any] = field(default_factory=dict)
    """Scores, tier, latency: written to the JSON beside the card."""

    @property
    def target_type(self) -> str:
        return self.target.type if self.target else ""


def scale_rect(rect: Rect, image_width: int, window_width: int) -> tuple[int, int, int, int]:
    """A frame in points as a box (left, top, right, bottom) in a screenshot's pixels."""
    scale = image_width / (window_width or image_width)
    x, y, w, h = rect
    return round(x * scale), round(y * scale), round((x + w) * scale), round((y + h) * scale)


def _dashed_rectangle(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    color: tuple[int, int, int],
    width: int,
) -> None:
    left, top, right, bottom = box
    dash = 6 * width
    for start in range(left, right, dash * 2):
        end = min(start + dash, right)
        draw.line((start, top, end, top), fill=color, width=width)
        draw.line((start, bottom, end, bottom), fill=color, width=width)
    for start in range(top, bottom, dash * 2):
        end = min(start + dash, bottom)
        draw.line((left, start, left, end), fill=color, width=width)
        draw.line((right, start, right, end), fill=color, width=width)


def annotate(
    png: bytes,
    rect: Rect | None,
    window_width: int,
    color: tuple[int, int, int],
    dashed: bool = False,
    action: str | None = None,
    typed: str | None = None,
) -> Image.Image:
    """The screenshot with `rect` boxed and, for an action, its marker drawn.

    Tap and toggle get a crosshair at the tap point, the centre of the frame. Typing gets the
    typed text as a label under the box.
    """
    image = Image.open(io.BytesIO(png)).convert("RGB")
    if rect is None:
        return image
    width = max(2, image.width // 200)
    draw = ImageDraw.Draw(image)
    box = scale_rect(rect, image.width, window_width)
    if dashed:
        _dashed_rectangle(draw, box, color, width)
    else:
        draw.rectangle(box, outline=color, width=width)
    if action in ("tap", "toggle"):
        cx, cy = scale_rect((*_point(center(rect)), 0, 0), image.width, window_width)[:2]
        arm = width * 8
        draw.line((cx - arm, cy, cx + arm, cy), fill=color, width=width)
        draw.line((cx, cy - arm, cx, cy + arm), fill=color, width=width)
        draw.ellipse((cx - arm // 2, cy - arm // 2, cx + arm // 2, cy + arm // 2), outline=color)
    elif action == "type text into" and typed is not None:
        font = ImageFont.load_default(size=max(14, image.width // 30))
        text = f"typed {typed!r}"
        left, _, right, bottom = draw.textbbox((0, 0), text, font=font)
        y = box[3] + width * 2
        draw.rectangle((box[0], y, box[0] + right - left + 8, y + bottom + 6), fill=color)
        draw.text((box[0] + 4, y + 2), text, fill="white", font=font)
    return image


def _point(xy: tuple[float, float]) -> tuple[int, int]:
    return round(xy[0]), round(xy[1])


def _what(element_type: str, label: str, name: str) -> str:
    ident = f"({name})" if name and name != label else "(no id)"
    return f"{short(element_type)} {label!r} {ident}" if label else f"{short(element_type)} {ident}"


def describe_change(fp: Fingerprint | None, target: Target | None) -> str:
    """What changed between the fingerprint and the element chosen now, in one sentence."""
    if fp is None:
        return "No fingerprint was recorded for this element."
    was = _what(fp.type, fp.label, fp.name)
    if target is None:
        return f"{was} wasn't found on this screen."
    now = _what(target.type, target.label, target.name)
    (ox, oy), (nx, ny) = center(fp.rect), center(target.rect)
    moves = []
    if abs(ny - oy) >= 4:
        moves.append(f"{abs(ny - oy):.0f} pt {'down' if ny > oy else 'up'}")
    if abs(nx - ox) >= 4:
        moves.append(f"{abs(nx - ox):.0f} pt {'right' if nx > ox else 'left'}")
    moved = f", moved {' and '.join(moves)}" if moves else ""
    if now == was:
        return f"{was} is unchanged{moved}." if moved else f"{was} is unchanged."
    return f"{was} is now {now}{moved}."


def describe(evidence: Evidence, verdict: Verdict) -> str:
    """The change for a HEAL; for ESCALATE and HALT, the change and why it wasn't trusted."""
    change = describe_change(evidence.fingerprint, evidence.target)
    if verdict.decision == HEAL:
        return change
    label = "Needs review" if verdict.decision == ESCALATE else "Likely real bug"
    return f"{change} {label}: {verdict.reason}."


def _fit(image: Image.Image, width: int) -> Image.Image:
    return image.resize(
        (width, round(image.height * width / image.width)), Image.Resampling.LANCZOS
    )


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def render(evidence: Evidence, verdict: Verdict) -> bytes:
    """The card: a header with the verdict and description over before, after and result."""
    color = COLORS[verdict.decision]
    fp, target = evidence.fingerprint, evidence.target
    panels: list[tuple[str, Image.Image | None]] = [
        (
            "Before: last green run",
            annotate(evidence.before, fp.rect if fp else None, evidence.window_width, GRAY, True)
            if evidence.before
            else None,
        ),
        (
            "After: " + (f"{verdict.decision} target" if target else "nothing chosen"),
            annotate(
                evidence.after,
                target.rect if target else (fp.rect if fp else None),
                evidence.window_width,
                color if target else GRAY,
                dashed=target is None,
                action=evidence.action if target else None,
                typed=evidence.typed,
            )
            if evidence.after
            else None,
        ),
        (
            "Result: "
            + (evidence.actual_screen.describe() if evidence.actual_screen else "not reached")
            + (
                f" (expected {evidence.expected_screen.describe().removeprefix('the ')})"
                if evidence.expected_screen and evidence.actual_screen
                else ""
            ),
            Image.open(io.BytesIO(evidence.result)).convert("RGB") if evidence.result else None,
        ),
    ]
    fitted = [(caption, _fit(img, PANEL_WIDTH) if img else None) for caption, img in panels]
    panel_height = max((img.height for _, img in fitted if img), default=PANEL_WIDTH)
    width = MARGIN + len(fitted) * (PANEL_WIDTH + MARGIN)
    card = Image.new("RGB", (width, HEADER + panel_height + CAPTION + MARGIN), BACKGROUND)
    draw = ImageDraw.Draw(card)
    title_font = ImageFont.load_default(size=22)
    body_font = ImageFont.load_default(size=14)
    draw.rectangle((0, 0, width, 6), fill=color)
    draw.text(
        (MARGIN, 14),
        f"{verdict.decision}  {evidence.key}  ({evidence.action})",
        fill=color,
        font=title_font,
    )
    for i, line in enumerate(
        _wrap(draw, describe(evidence, verdict), body_font, width - 2 * MARGIN)[:3]
    ):
        draw.text((MARGIN, 44 + i * 17), line, fill=TEXT, font=body_font)
    for i, (caption, img) in enumerate(fitted):
        x = MARGIN + i * (PANEL_WIDTH + MARGIN)
        if img is None:
            draw.rectangle((x, HEADER, x + PANEL_WIDTH, HEADER + panel_height), outline=GRAY)
            draw.text((x + 12, HEADER + 12), "no screenshot", fill=GRAY, font=body_font)
        else:
            card.paste(img, (x, HEADER))
        for j, line in enumerate(_wrap(draw, caption, body_font, PANEL_WIDTH)[:2]):
            draw.text((x, HEADER + panel_height + 6 + j * 17), line, fill=TEXT, font=body_font)
    out = io.BytesIO()
    card.save(out, format="PNG")
    return out.getvalue()


def save(evidence: Evidence, verdict: Verdict, out_dir: Path) -> Path:
    """Write card.png, the raw screenshots and evidence.json to `out_dir`; return the card."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("before", "after", "result"):
        png = getattr(evidence, name)
        if png:
            (out_dir / f"{name}.png").write_bytes(png)
    card = out_dir / "card.png"
    card.write_bytes(render(evidence, verdict))
    fp, target = evidence.fingerprint, evidence.target
    data = {
        "key": evidence.key,
        "action": evidence.action,
        "verdict": verdict.decision,
        "reason": verdict.reason,
        "description": describe(evidence, verdict),
        "checks": verdict.checks_json(),
        "fingerprint": json.loads(fp.to_json()) if fp else None,
        "target": target.__dict__ if target else None,
        "typed": evidence.typed,
        "expected_screen": evidence.expected_screen.to_dict() if evidence.expected_screen else None,
        "actual_screen": evidence.actual_screen.to_dict() if evidence.actual_screen else None,
        **evidence.data,
    }
    (out_dir / "evidence.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return card


def locator_patch(yaml_text: str, path: str, name: str, strategy: str, value: str) -> str | None:
    """A unified diff that points logical locator `name` at the healed element.

    Returns None if the entry isn't a plain top-level ``name:`` entry (a template with
    placeholders, say), since a healed concrete value can't replace a template.
    """
    lines = yaml_text.splitlines(keepends=True)
    start = next(
        (i for i, line in enumerate(lines) if re.match(rf"{re.escape(name)}:", line)), None
    )
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and (lines[end].startswith((" ", "\t")) or not lines[end].strip()):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    old = yaml.safe_load("".join(lines[start:end]))[name]
    if isinstance(old, str) and "{" in old:
        return None
    intent = old.get("intent") if isinstance(old, dict) else None
    spec: dict[str, str] = {strategy: value}
    if intent:
        spec["intent"] = intent
    if strategy == "id" and not intent:
        entry = yaml.safe_dump(value, width=1000).splitlines()[0]
    else:
        entry = yaml.safe_dump(spec, default_flow_style=True, sort_keys=False, width=1000).strip()
    new = f"{name}: {entry}\n"
    patched = lines[:start] + [new] + lines[end:]
    return "".join(difflib.unified_diff(lines, patched, fromfile=f"a/{path}", tofile=f"b/{path}"))
