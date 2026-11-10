"""Healing tiers."""

from collections.abc import Callable
from typing import Any, cast

from appium.webdriver.webelement import WebElement

from healorhalt.fingerprint import Node, parse
from healorhalt.heal import tier0_cache, tier1_heuristic
from healorhalt.locator import Locator
from healorhalt.store import HealRecord, Store

DEFAULT_THRESHOLD = 0.6

TryFind = Callable[[Locator], WebElement | None]
"""Looks a locator up once, without healing; None when nothing matches."""


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

    Every attempt, successful or not, is written to the audit log. There is no verdict engine
    yet (Phase 4), so a Tier 1 heal is accepted whenever its score clears the threshold.
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
    ) -> None:
        self.store = store
        self.app = app
        self.mode = mode
        self.run_id = run_id
        self.test = test
        self.mutation = mutation
        self.threshold = threshold

    def _log(self, locator: Locator, outcome: str, **fields: Any) -> None:
        self.store.log_heal(
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

    def heal(self, locator: Locator, page_source: str, try_find: TryFind) -> WebElement | None:
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
