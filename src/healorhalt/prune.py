"""Cuts an XCUITest page source down to what the LLM tier needs to see.

A full page source runs to tens of thousands of tokens. The LLM needs only the elements the
locator could now mean, numbered so they match the boxes drawn on the screenshot, plus the text
around where the element used to be. Everything is budgeted to about TOKEN_BUDGET tokens.
"""

from dataclasses import dataclass

from healorhalt.fingerprint import (
    INTERACTIVE,
    Fingerprint,
    Node,
    Rect,
    candidates,
    distance,
    is_visible,
    rect_of,
    short_type,
)
from healorhalt.heal import tier1_heuristic

TOKEN_BUDGET = 2000
CHARS_PER_TOKEN = 4
"""A rough figure for short English and identifiers, good enough for budgeting."""
MAX_MARKS = 15
EXTRA_MARKS = 5
"""Non-interactive candidates kept, best Tier 1 score first, on top of every interactive one."""


@dataclass(frozen=True)
class Mark:
    """A numbered candidate: the same number labels its box on the screenshot."""

    id: int
    node: Node
    rect: Rect

    def describe(self) -> dict[str, object]:
        return {
            "mark": self.id,
            "type": str(self.node.tag),
            "name": self.node.get("name", ""),
            "label": self.node.get("label", ""),
        }


def estimate_tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)


def window_rect(root: Node) -> Rect:
    app = next(root.iter("XCUIElementTypeApplication"), None)
    return rect_of(app) if app is not None else (0, 0, 0, 0)


def _on_screen(rect: Rect, window: Rect) -> bool:
    x, y, width, height = rect
    wx, wy, ww, wh = window
    return (
        width > 0
        and height > 0
        and x < wx + ww
        and y < wy + wh
        and x + width > wx
        and y + height > wy
    )


def select_marks(fp: Fingerprint, root: Node, limit: int = MAX_MARKS) -> list[Mark]:
    """The elements the locator could now mean, numbered in reading order.

    Every on-screen interactive element is kept, since the right one may score badly on the
    heuristics (that is why the LLM was called), plus the best few others by Tier 1 score. Of
    elements sharing one frame, such as a cell and the button inside it, only the best-scoring is
    kept. Numbers follow reading order, so they say nothing about the heuristic ranking.
    """
    window = window_rect(root)
    scored = [
        tier1_heuristic.score(fp, node, root)
        for node in candidates(root)
        if _on_screen(rect_of(node), window)
    ]
    scored.sort(key=lambda c: c.score, reverse=True)
    by_rect: dict[Rect, Node] = {}
    extras = 0
    for candidate in scored:
        rect = rect_of(candidate.node)
        if rect in by_rect:
            continue
        if candidate.node.tag not in INTERACTIVE:
            if extras == EXTRA_MARKS:
                continue
            extras += 1
        by_rect[rect] = candidate.node
        if len(by_rect) == limit:
            break
    ordered = sorted(by_rect.items(), key=lambda item: (item[0][1], item[0][0]))
    return [Mark(i, node, rect) for i, (rect, node) in enumerate(ordered, start=1)]


def _container(node: Node) -> str:
    """The nearest named ancestor, which often says which part of the screen this is."""
    for parent in node.iterancestors():
        name = parent.get("name")
        if name and parent.tag not in ("XCUIElementTypeApplication", "XCUIElementTypeWindow"):
            return f"{short_type(parent)} {name!r}"
    return ""


def element_line(node: Node, rect: Rect) -> str:
    parts = [short_type(node)]
    name, label, value = node.get("name", ""), node.get("label", ""), node.get("value", "")
    if name and name != label:
        parts.append(f"id={name!r}")
    if label:
        parts.append(f"label={label!r}")
    if value and value != label:
        parts.append(f"value={value[:40]!r}")
    parts.append("at ({}, {}, {}x{})".format(*rect))
    if not is_visible(node):
        parts.append("hidden")
    container = _container(node)
    if container:
        parts.append(f"in {container}")
    return " ".join(parts)


def describe_fingerprint(fp: Fingerprint) -> str:
    parts = [fp.type.removeprefix("XCUIElementType")]
    if fp.name and fp.name != fp.label:
        parts.append(f"id={fp.name!r}")
    if fp.label:
        parts.append(f"label={fp.label!r}")
    parts.append("at ({}, {}, {}x{})".format(*fp.rect))
    named = [a.split(":", 1) for a in fp.ancestors if not a.endswith(":")]
    if named:
        kind, name = named[0]
        parts.append(f"in {kind} {name!r}")
    line = " ".join(parts)
    if fp.neighbors:
        line += "; nearby text: " + ", ".join(repr(n) for n in fp.neighbors)
    return line


def context_lines(fp: Fingerprint, root: Node, marks: list[Mark], budget_chars: int) -> list[str]:
    """Visible text outside the marks, nearest the element's old position first, within budget."""
    marked = {node for mark in marks for node in mark.node.iter()}
    window = window_rect(root)
    texts = [
        node
        for node in root.iter()
        if node.tag in ("XCUIElementTypeStaticText", "XCUIElementTypeNavigationBar")
        and node not in marked
        and is_visible(node)
        and (node.get("label") or node.get("name"))
        and _on_screen(rect_of(node), window)
    ]
    texts.sort(key=lambda node: distance(fp.rect, rect_of(node)))
    lines: list[str] = []
    seen: set[str] = set()
    used = 0
    for node in texts:
        line = element_line(node, rect_of(node))
        text = node.get("label") or node.get("name") or ""
        if text in seen:
            continue
        if used + len(line) + 1 > budget_chars:
            break
        seen.add(text)
        lines.append(line)
        used += len(line) + 1
    return lines


@dataclass(frozen=True)
class PrunedScreen:
    marks: list[Mark]
    elements: str
    """One numbered line per mark."""
    context: str
    """Other visible text, nearest the element's old position first."""

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.elements) + estimate_tokens(self.context)


def prune(fp: Fingerprint, root: Node, token_budget: int = TOKEN_BUDGET) -> PrunedScreen:
    marks = select_marks(fp, root)
    elements = "\n".join(f"[{m.id}] {element_line(m.node, m.rect)}" for m in marks)
    remaining = token_budget * CHARS_PER_TOKEN - len(elements)
    context = "\n".join(context_lines(fp, root, marks, max(0, remaining)))
    return PrunedScreen(marks, elements, context)
