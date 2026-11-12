"""Healing tiers."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from appium.webdriver.webelement import WebElement

from healorhalt.fingerprint import Fingerprint, Node, parse
from healorhalt.heal import tier0_cache, tier1_heuristic
from healorhalt.llm.ollama_client import DEFAULT_MODEL
from healorhalt.locator import Locator
from healorhalt.store import HealRecord, Store

if TYPE_CHECKING:
    from healorhalt.heal.tier2_llm import ChatClient

DEFAULT_THRESHOLD = 0.6
LLM_MIN_CONFIDENCE = 0.5
"""Lowest model confidence a Tier 2 heal is accepted at, until the Phase 4 verdict engine."""

TryFind = Callable[[Locator], WebElement | None]
"""Looks a locator up once, without healing; None when nothing matches."""
Screenshot = Callable[[], bytes]


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
    return "xpath", node.getroottree().getpath(node)


class Healer:
    """Repairs a locator that found nothing, trying the enabled tiers cheapest first.

    Modes: ``cache`` uses Tier 0 only, ``heuristic`` adds Tier 1, and ``llm`` adds Tier 2, which
    is asked only when Tier 1 finds nothing that clears its threshold.

    Every attempt, successful or not, is written to the audit log. There is no verdict engine
    yet (Phase 4), so a Tier 1 heal is accepted whenever its score clears the threshold, and a
    Tier 2 heal whenever the model says the element exists with at least LLM_MIN_CONFIDENCE.
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
    ) -> None:
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

    def heal(
        self,
        locator: Locator,
        page_source: str,
        try_find: TryFind,
        screenshot: Screenshot | None = None,
        action: str = "find",
    ) -> WebElement | None:
        """A working element for `locator`, or None; Tier 2 is told the step's `action`."""
        cached = tier0_cache.lookup(self.store, self.app, locator)
        if cached is not None:
            element = try_find(cached)
            if element is not None:
                self._log(
                    locator,
                    "healed",
                    tier=0,
                    new_strategy=cached.strategy,
                    new_value=cached.value,
                    score=1.0,
                )
                return element
        if self.mode == "cache":
            self._log(locator, "not_found", tier=0)
            return None

        fp = self.store.fingerprint(self.app, locator.key)
        if fp is None:
            self._log(locator, "no_fingerprint", tier=1)
            return None
        root = parse(page_source)
        element = self._tier1(locator, fp, root, try_find)
        if element is not None or self.mode != "llm":
            return element
        return self._tier2(locator, fp, root, try_find, screenshot, action)

    def _tier1(
        self, locator: Locator, fp: Fingerprint, root: Node, try_find: TryFind
    ) -> WebElement | None:
        ranked = tier1_heuristic.rank(fp, root)
        described = [c.describe() for c in ranked]
        if not ranked or ranked[0].score < self.threshold:
            top = ranked[0].score if ranked else None
            self._log(locator, "below_threshold", tier=1, score=top, candidates=described)
            return None
        best = ranked[0]
        strategy, value = locator_for(best.node, root)
        element = try_find(locator.replaced(strategy, value))
        self._log(
            locator,
            "healed" if element is not None else "not_found",
            tier=1,
            new_strategy=strategy,
            new_value=value,
            score=best.score,
            candidates=described,
        )
        return element

    def _tier2(
        self,
        locator: Locator,
        fp: Fingerprint,
        root: Node,
        try_find: TryFind,
        screenshot: Screenshot | None,
        action: str,
    ) -> WebElement | None:
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
        if proposal is None:
            outcome = "llm_error"
        else:
            fields["score"] = proposal.confidence
            described.append({"proposal": proposal.model_dump()})
            mark = tier2_llm.chosen(screen, proposal)
            if mark is None:
                outcome = "no_proposal"
            elif proposal.confidence < self.llm_min_confidence:
                outcome = "low_confidence"
        element = None
        if (
            mark is not None
            and proposal is not None
            and proposal.confidence >= self.llm_min_confidence
        ):
            strategy, value = locator_for(mark.node, root)
            element = try_find(locator.replaced(strategy, value))
            outcome = "healed" if element is not None else "not_found"
            fields |= {"new_strategy": strategy, "new_value": value}
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
        return element
