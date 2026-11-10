"""Tier 1: heuristic fingerprint matching."""

from dataclasses import dataclass

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from healorhalt.fingerprint import (
    Fingerprint,
    Node,
    ancestors_of,
    candidates,
    distance,
    is_visible,
    neighbors_of,
    rect_of,
)

WEIGHTS = {
    "label": 0.30,
    "id": 0.25,
    "type": 0.15,
    "position": 0.10,
    "ancestors": 0.10,
    "neighbors": 0.10,
}
# Element types that stand in for each other often enough to earn partial credit.
TAPPABLE = {"XCUIElementTypeButton", "XCUIElementTypeCell", "XCUIElementTypeLink"}
POSITION_RANGE = 300.0
"""Points of movement at which the position score reaches zero."""
HIDDEN_PENALTY = 0.85
"""Multiplier for elements XCUITest reports as not visible."""


@dataclass(frozen=True)
class Candidate:
    node: Node
    score: float
    parts: dict[str, float]

    def describe(self) -> dict[str, object]:
        return {
            "type": str(self.node.tag),
            "name": self.node.get("name", ""),
            "label": self.node.get("label", ""),
            "score": round(self.score, 3),
            "parts": {k: round(v, 3) for k, v in self.parts.items()},
        }


def _text(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return fuzz.ratio(a.lower(), b.lower()) / 100


def _type(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return 0.5 if a in TAPPABLE and b in TAPPABLE else 0.0


def _overlap(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    if not a and not b:
        return 1.0
    return len(set(a) & set(b)) / len(set(a) | set(b))


def score(fp: Fingerprint, node: Node, root: Node) -> Candidate:
    parts = {
        "label": _text(fp.label, node.get("label", "")),
        "id": _text(fp.name, node.get("name", "")),
        "type": _type(fp.type, str(node.tag)),
        "position": max(0.0, 1 - distance(fp.rect, rect_of(node)) / POSITION_RANGE),
        "ancestors": Levenshtein.normalized_similarity(fp.ancestors, ancestors_of(node)),
        "neighbors": _overlap(fp.neighbors, neighbors_of(node, root)),
    }
    total = sum(WEIGHTS[k] * v for k, v in parts.items())
    if not is_visible(node):
        total *= HIDDEN_PENALTY
    return Candidate(node, total, parts)


def rank(fp: Fingerprint, root: Node, limit: int = 5) -> list[Candidate]:
    """Score every plausible element on screen against the fingerprint, best first."""
    scored = [score(fp, node, root) for node in candidates(root)]
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:limit]
