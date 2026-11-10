"""Tier 0: reuse previously approved heals."""

from healorhalt.locator import Locator
from healorhalt.store import Store


def lookup(store: Store, app: str, locator: Locator) -> Locator | None:
    """The approved replacement for a broken locator, if a reviewer has approved one."""
    cached = store.cached(app, locator.key)
    if cached is None:
        return None
    strategy, value = cached
    return locator.replaced(strategy, value)
