"""HEAL / ESCALATE / HALT: whether a healed step may count as a pass.

A heal proposal, from any tier, is checked in three rounds:

1. Before the action: exactly one element matches the new locator, its type matches the
   fingerprint, it is enabled, the combined score clears the thresholds, no runner-up is nearly as
   good, and (Tier 2) the LLM says the element still exists.
2. After the action: the action took effect (typed text shows up, a switch flipped) and the screen
   reached matches the one recorded on the last green run at this same step.
3. When the test ends: the test passed. A heal followed by a failing test was a heal past a bug.

A failed hard check is HALT. A failed soft check, or a check that couldn't run, is ESCALATE: the
test goes on, but the heal waits for a person. Only a heal that passes everything is HEAL. The
LLM's confidence feeds the combined score and never decides on its own: with no heuristic support
it can't reach the HEAL threshold.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Literal

from healorhalt.fingerprint import Fingerprint, Node, is_visible

Decision = Literal["HEAL", "ESCALATE", "HALT"]
HEAL: Decision = "HEAL"
ESCALATE: Decision = "ESCALATE"
HALT: Decision = "HALT"

TAPPABLE = {"XCUIElementTypeButton", "XCUIElementTypeCell", "XCUIElementTypeLink"}
"""Types that stand in for each other: a change among them is ESCALATE, not HALT (catalog B5)."""
EFFECT_ACTIONS = {"tap", "toggle", "type text into"}
"""Actions that change the app, so the element must be enabled and the result is checked."""


@dataclass(frozen=True)
class VerdictConfig:
    heal_threshold: float = 0.6
    """Combined score at or above which a heal may be HEAL."""
    floor: float = 0.4
    """Combined score below which a heal is HALT; in between is ESCALATE."""
    llm_weight: float = 0.5
    """Share of a Tier 2 combined score that comes from the LLM's confidence; the rest is the
    chosen element's Tier 1 score. At 0.5, confidence alone tops out at 0.5, under the threshold."""
    ambiguity_margin: float = 0.05
    """A Tier 1 runner-up within this much of the best makes the heal ambiguous (catalog R7)."""
    screen_similarity: float = 0.5
    """Least Jaccard overlap of visible identifiers for two snapshots to count as one screen."""


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool | None
    """None when the check couldn't run, e.g. no screen was recorded for this step."""
    detail: str
    hard: bool = True
    """A failed hard check is HALT; a failed soft check is ESCALATE."""


@dataclass
class Verdict:
    decision: Decision
    reason: str
    checks: list[Check] = field(default_factory=list)

    def checks_json(self) -> list[dict[str, object]]:
        return [asdict(c) for c in self.checks]


def decide(checks: list[Check]) -> Verdict:
    """HALT on any failed hard check, ESCALATE on a failed soft or unrun check, else HEAL."""
    hard = [c for c in checks if c.passed is False and c.hard]
    if hard:
        return Verdict(HALT, hard[0].detail, checks)
    doubtful = [c for c in checks if c.passed is not True]
    if doubtful:
        return Verdict(ESCALATE, doubtful[0].detail, checks)
    return Verdict(HEAL, "every check passed", checks)


def combined_score(
    tier: int, tier1_score: float | None, llm_confidence: float | None, config: VerdictConfig
) -> float:
    """Tier 0 heals were approved by a person; Tier 1 has its score; Tier 2 blends both."""
    if tier == 0:
        return 1.0
    t1 = tier1_score or 0.0
    if tier == 1 or llm_confidence is None:
        return t1
    return (1 - config.llm_weight) * t1 + config.llm_weight * llm_confidence


# Round 1: before the action.


def check_unique(count: int | None) -> Check:
    if count is None:
        return Check("unique", None, "couldn't count matches for the new locator", hard=False)
    return Check("unique", count == 1, f"the new locator matches {count} elements")


def check_type(fp: Fingerprint, node: Node) -> Check:
    was, now = fp.type, str(node.tag)
    if was == now:
        return Check("type", True, f"still a {short(now)}")
    if was in TAPPABLE and now in TAPPABLE:
        return Check("type", False, f"was a {short(was)}, now a {short(now)}", hard=False)
    return Check("type", False, f"was a {short(was)}, now a {short(now)}")


def check_score(score: float, config: VerdictConfig) -> Check:
    detail = f"combined score {score:.2f}"
    if score >= config.heal_threshold:
        return Check("score", True, f"{detail} clears {config.heal_threshold:.2f}")
    if score >= config.floor:
        return Check("score", False, f"{detail} is under {config.heal_threshold:.2f}", hard=False)
    return Check("score", False, f"{detail} is under the floor of {config.floor:.2f}")


def check_ambiguity(best: float, runner_up: float | None, config: VerdictConfig) -> Check:
    if runner_up is None or best - runner_up > config.ambiguity_margin:
        return Check("ambiguity", True, "no other element scores close")
    return Check(
        "ambiguity",
        False,
        f"another element scores {runner_up:.2f} against {best:.2f}",
        hard=False,
    )


def check_llm_exists(still_exists: bool) -> Check:
    if still_exists:
        return Check("llm", True, "the LLM says the element is still on screen")
    return Check("llm", False, "the LLM says the element is gone")


def check_enabled(action: str, enabled: bool) -> Check:
    if action not in EFFECT_ACTIONS or enabled:
        return Check("enabled", True, "the element is enabled")
    return Check("enabled", False, f"can't {action} it: the element is disabled")


# Round 2: after the action.


def check_effect(action: str, element_type: str, wanted: str | None, now: str | None) -> Check:
    """Typed text shows up in the field (as one dot per character in a secure field), and a
    toggled switch changed value. Other actions have no effect to check here.

    For "toggle", `wanted` is the value before the tap: anything else counts as flipped.
    """
    if action == "type text into":
        if element_type == "XCUIElementTypeSecureTextField":
            ok = now is not None and len(now) == len(wanted or "") and now != wanted
        else:
            ok = now == wanted
        return Check("effect", ok, f"typed {wanted!r}; the field shows {now!r}")
    if action == "toggle":
        return Check("effect", wanted != now, f"the switch went from {wanted!r} to {now!r}")
    return Check("effect", True, f"nothing to check after '{action}'")


@dataclass(frozen=True)
class Screen:
    """A snapshot of which screen is showing: the screen marker found, and every visible
    identifier, which also tells unmarked states (an alert, edit mode) apart."""

    name: str | None
    ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "ids": list(self.ids)}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "Screen":
        name, ids = data.get("name"), data.get("ids")
        return cls(
            str(name) if name is not None else None,
            tuple(str(i) for i in ids) if isinstance(ids, list) else (),
        )

    def describe(self) -> str:
        return f"the {self.name} screen" if self.name else "an unmarked screen"


def screen_of(root: Node, markers: Mapping[str, str]) -> Screen:
    """The screen in a page source. `markers` maps screen names to their marker identifiers.

    Only developer-set identifiers count: XCUITest reports an element with none under its label,
    and labels carry data (item names, counts) that changes without the screen changing.
    """
    ids: set[str] = set()
    for node in root.iter():
        name, label = node.get("name"), node.get("label")
        if name and name != label and is_visible(node) and not _system_input(node):
            ids.add(name)
    found = [screen for screen, marker in markers.items() if marker in ids]
    return Screen(found[0] if len(found) == 1 else None, tuple(sorted(ids)))


SYSTEM_INPUT = {"XCUIElementTypeKeyboard"}
SYSTEM_INPUT_NAMES = {"SystemInputAssistantView"}


def _system_input(node: Node) -> bool:
    """Whether the node is the keyboard or its suggestion bar, which come and go with focus."""
    for n in [node, *node.iterancestors()]:
        if n.tag in SYSTEM_INPUT or n.get("name") in SYSTEM_INPUT_NAMES:
            return True
    return False


def similarity(a: Screen, b: Screen) -> float:
    if not a.ids and not b.ids:
        return 1.0
    return len(set(a.ids) & set(b.ids)) / len(set(a.ids) | set(b.ids))


def check_screen(expected: Screen | None, actual: Screen, config: VerdictConfig) -> Check:
    if expected is None:
        return Check(
            "next_screen", None, "no green run recorded the screen after this step", hard=False
        )
    if expected.name and actual.name and expected.name != actual.name:
        return Check(
            "next_screen",
            False,
            f"expected {expected.describe()} after this step; got {actual.describe()}",
        )
    overlap = similarity(expected, actual)
    if overlap < config.screen_similarity:
        return Check(
            "next_screen",
            False,
            f"the screen after this step shares {overlap:.0%} of its identifiers with the "
            f"recorded one ({expected.describe()})",
        )
    return Check("next_screen", True, f"reached {actual.describe()} ({overlap:.0%} alike)")


# Round 3: when the test ends.


def check_replay(test_passed: bool, failure: str = "") -> Check:
    if test_passed:
        return Check("replay", True, "the test passed after this heal")
    lines = [line for line in failure.strip().splitlines() if line.strip()]
    first = lines[-1].strip() if lines else "the test failed"
    return Check("replay", False, f"the test failed after this heal: {first}")


def short(element_type: str) -> str:
    return element_type.removeprefix("XCUIElementType")


class HealHalted(AssertionError):
    """A heal was refused: the test stops here with a "likely real bug" message."""
