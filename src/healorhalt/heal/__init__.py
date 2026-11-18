"""Healing tiers."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from appium.webdriver.webelement import WebElement

from healorhalt.fingerprint import Fingerprint, Node, parse
from healorhalt.heal import tier0_cache, tier1_heuristic
from healorhalt.llm.ollama_client import DEFAULT_MODEL
from healorhalt.locator import Locator
from healorhalt.store import HealRecord, Store
from healorhalt.verdict import (
    Check,
    VerdictConfig,
    check_ambiguity,
    check_llm_exists,
    check_score,
    check_type,
    check_unique,
    combined_score,
)

if TYPE_CHECKING:
    from healorhalt.heal.tier2_llm import ChatClient

DEFAULT_THRESHOLD = 0.6
LLM_MIN_CONFIDENCE = 0.5
"""Lowest model confidence a Tier 2 heal is accepted at in llm mode, which has no verdict
engine. In full mode the verdict engine decides instead."""
MODES = ("cache", "heuristic", "llm", "full")

TryFind = Callable[[Locator], WebElement | None]
"""Looks a locator up once, without healing; None when nothing matches."""
Screenshot = Callable[[], bytes]
Count = Callable[[Locator], int]
"""How many elements a locator matches right now."""


@dataclass
class Heal:
    """A working element for a broken locator, and what the verdict engine needs to judge it."""

    element: WebElement
    locator: Locator
    """The healed locator."""
    heal_id: int
    tier: int
    score: float
    """The combined score: Tier 1's score, or for Tier 2 a blend of it and the LLM's confidence."""
    fingerprint: Fingerprint | None = None
    node: Node | None = None
    checks: list[Check] = field(default_factory=list)
    """Round 1 of the verdict (full mode only): checks made before the action."""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Miss:
    """A heal that found nothing: the last attempt's id and why."""

    heal_id: int
    reason: str
    fingerprint: Fingerprint | None


def _quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")


def _count(root: Node, xpath: str, value: str) -> int:
    return len(cast(list[Node], root.xpath(xpath, v=value)))


def locator_for(node: Node, root: Node) -> tuple[str, str]:
    """The most stable (strategy, value) that picks out exactly this node."""
    name, label, element_type = node.get("name", ""), node.get("label", ""), str(node.tag)
    if name and name != label and _count(root, "//*[@name=$v]", name) == 1:
        return "id", name
    if label and _count(root, f"//{element_type}[@label=$v]", label) == 1:
        return "predicate", f"type == '{element_type}' AND label == '{_quote(label)}'"
    # Several match: pick this one by its position among them. A path relative to the type and
    # label survives wrappers that an absolute path from the root does not.
    if label and ('"' not in label or "'" not in label):
        literal = f'"{label}"' if '"' not in label else f"'{label}'"
        same = cast(list[Node], root.xpath(f"//{element_type}[@label=$v]", v=label))
        return "xpath", f"(//{element_type}[@label={literal}])[{same.index(node) + 1}]"
    same = cast(list[Node], root.xpath(f"//{element_type}"))
    return "xpath", f"(//{element_type})[{same.index(node) + 1}]"


class Healer:
    """Repairs a locator that found nothing, trying the enabled tiers cheapest first.

    Modes: ``cache`` uses Tier 0 only, ``heuristic`` adds Tier 1, and ``llm`` adds Tier 2, which
    is asked only when Tier 1 finds nothing that clears its threshold. ``full`` runs the same
    tiers as ``llm`` and hands every proposal to the verdict engine (healorhalt.verdict): the
    healer makes the checks that need only the page source, and the session the rest.

    Every attempt, successful or not, is written to the audit log. Without the verdict engine a
    Tier 1 heal is accepted whenever its score clears the threshold, and a Tier 2 heal whenever
    the model says the element exists with at least LLM_MIN_CONFIDENCE.
    """

    def __init__(
        self,
        store: Store,
        app: str,
        mode: str,
        run_id: str,
        test: str,
        mutation: str | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        llm_client: "ChatClient | None" = None,
        llm_model: str | None = None,
        llm_min_confidence: float = LLM_MIN_CONFIDENCE,
        verdict: VerdictConfig | None = None,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown heal mode {mode!r}")
        self.store = store
        self.app = app
        self.mode = mode
        self.run_id = run_id
        self.test = test
        self.mutation = mutation
        self.threshold = threshold
        self.llm_client = llm_client
        self.llm_model = llm_model
        self.llm_min_confidence = llm_min_confidence
        self.verdict = verdict or VerdictConfig()
        self.miss: Miss | None = None
        """Why the last call to heal() found nothing."""

    @property
    def verifies(self) -> bool:
        """Whether heals go through the verdict engine."""
        return self.mode == "full"

    def _log(self, locator: Locator, outcome: str, **fields: Any) -> int:
        return self.store.log_heal(
            HealRecord(
                run_id=self.run_id,
                app=self.app,
                test=self.test,
                mutation=self.mutation,
                key=locator.key,
                outcome=outcome,
                old_strategy=locator.strategy,
                old_value=locator.value,
                **fields,
            )
        )

    def _missed(self, heal_id: int, reason: str, fp: Fingerprint | None = None) -> None:
        self.miss = Miss(heal_id, reason, fp)

    def heal(
        self,
        locator: Locator,
        page_source: str,
        try_find: TryFind,
        screenshot: Screenshot | None = None,
        action: str = "find",
        count: Count | None = None,
    ) -> Heal | None:
        """A working element for `locator`, or None; Tier 2 is told the step's `action`.

        `count` lets full mode check that the healed locator matches exactly one element.
        """
        self.miss = None
        cached = tier0_cache.lookup(self.store, self.app, locator)
        if cached is not None:
            element = try_find(cached)
            if element is not None:
                heal_id = self._log(
                    locator,
                    "healed",
                    tier=0,
                    new_strategy=cached.strategy,
                    new_value=cached.value,
                    score=1.0,
                )
                approved = Heal(element, cached, heal_id, 0, 1.0)
                approved.fingerprint = self.store.fingerprint(self.app, locator.key)
                if self.verifies:
                    approved.checks = [check_unique(count(cached) if count else None)]
                return approved
        if self.mode == "cache":
            self._missed(self._log(locator, "not_found", tier=0), "no approved heal applies")
            return None

        fp = self.store.fingerprint(self.app, locator.key)
        if fp is None:
            heal_id = self._log(locator, "no_fingerprint", tier=1)
            self._missed(heal_id, "no fingerprint was recorded for this element")
            return None
        root = parse(page_source)
        heal = self._tier1(locator, fp, root, try_find, count)
        if heal is not None or self.mode == "heuristic":
            return heal
        if self.miss is not None and self.miss.reason.startswith("the healed locator"):
            return None
        return self._tier2(locator, fp, root, try_find, screenshot, action, count)

    def _tier1(
        self, locator: Locator, fp: Fingerprint, root: Node, try_find: TryFind, count: Count | None
    ) -> Heal | None:
        ranked = tier1_heuristic.rank(fp, root)
        described = [c.describe() for c in ranked]
        if not ranked or ranked[0].score < self.threshold:
            top = ranked[0].score if ranked else None
            heal_id = self._log(locator, "below_threshold", tier=1, score=top, candidates=described)
            shown = f"{top:.2f}" if top is not None else "none"
            self._missed(heal_id, f"no element scored {self.threshold:.2f} (best: {shown})", fp)
            return None
        best = ranked[0]
        strategy, value = locator_for(best.node, root)
        healed = locator.replaced(strategy, value)
        element = try_find(healed)
        heal_id = self._log(
            locator,
            "healed" if element is not None else "not_found",
            tier=1,
            new_strategy=strategy,
            new_value=value,
            score=best.score,
            candidates=described,
        )
        if element is None:
            self._missed(heal_id, f"the healed locator {strategy}={value} found nothing", fp)
            return None
        heal = Heal(element, healed, heal_id, 1, best.score, fp, best.node)
        if self.verifies:
            others = [c for c in ranked[1:] if not _related(c.node, best.node)]
            heal.checks = [
                check_unique(count(healed) if count else None),
                check_type(fp, best.node),
                check_score(best.score, self.verdict),
                check_ambiguity(best.score, others[0].score if others else None, self.verdict),
            ]
        return heal

    def _tier2(
        self,
        locator: Locator,
        fp: Fingerprint,
        root: Node,
        try_find: TryFind,
        screenshot: Screenshot | None,
        action: str,
        count: Count | None,
    ) -> Heal | None:
        from healorhalt.heal import tier2_llm  # imported late: it imports prune, which needs tier1

        client = self.llm_client or tier2_llm.make_client()
        model = self.llm_model or DEFAULT_MODEL
        png = screenshot() if screenshot is not None else None
        screen, call = tier2_llm.propose(
            fp, root, png, client, locator.name, locator.intent, action, model
        )
        described: list[dict[str, Any]] = [m.describe() for m in screen.marks]
        proposal = call.proposal
        fields: dict[str, Any] = {"tier": 2, "candidates": described}
        mark = None
        reason = ""
        if proposal is None:
            outcome, reason = "llm_error", f"the LLM gave no answer ({call.error})"
        else:
            fields["score"] = proposal.confidence
            described.append({"proposal": proposal.model_dump()})
            mark = tier2_llm.chosen(screen, proposal)
            if mark is None:
                outcome, reason = "no_proposal", "the LLM says the element is gone"
            elif not self.verifies and proposal.confidence < self.llm_min_confidence:
                outcome = "low_confidence"
                reason = f"the LLM is only {proposal.confidence:.0%} sure"
                mark = None
        element = None
        heal = None
        if mark is not None and proposal is not None:
            strategy, value = locator_for(mark.node, root)
            healed = locator.replaced(strategy, value)
            element = try_find(healed)
            outcome = "healed" if element is not None else "not_found"
            if element is None:
                reason = f"the healed locator {strategy}={value} found nothing"
            fields |= {"new_strategy": strategy, "new_value": value}
            if element is not None:
                tier1 = tier1_heuristic.score(fp, mark.node, root).score
                combined = combined_score(2, tier1, proposal.confidence, self.verdict)
                heal = Heal(element, healed, 0, 2, combined, fp, mark.node)
                heal.data = {
                    "tier1_score": round(tier1, 3),
                    "llm_confidence": proposal.confidence,
                    "llm_rationale": proposal.rationale,
                    "llm_latency_s": round(call.latency_s, 2),
                }
                if self.verifies:
                    heal.checks = [
                        check_unique(count(healed) if count else None),
                        check_type(fp, mark.node),
                        check_llm_exists(proposal.element_still_exists),
                        check_score(combined, self.verdict),
                    ]
        heal_id = self._log(locator, outcome, **fields)
        self.store.log_llm_call(
            heal_id,
            call.model,
            call.latency_s,
            call.prompt,
            call.prompt_tokens_estimate,
            call.response,
            call.prompt_tokens,
            call.completion_tokens,
            call.error,
        )
        if heal is None:
            self._missed(heal_id, reason, fp)
            return None
        heal.heal_id = heal_id
        return heal


def _related(a: Node, b: Node) -> bool:
    """Whether one node contains the other: a cell and its button are one candidate, not two."""
    return a in set(b.iterancestors()) or b in set(a.iterancestors())
