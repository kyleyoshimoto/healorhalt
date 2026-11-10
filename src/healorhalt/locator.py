"""Logical locators, e.g. L('login.submit'), mapped from YAML."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml
from appium.webdriver.common.appiumby import AppiumBy

STRATEGIES = {
    "id": AppiumBy.ACCESSIBILITY_ID,
    "predicate": AppiumBy.IOS_PREDICATE,
    "class_chain": AppiumBy.IOS_CLASS_CHAIN,
    "xpath": AppiumBy.XPATH,
}


class LocatorError(LookupError):
    """A locator file is malformed or a logical name is unknown."""


@dataclass(frozen=True)
class Locator:
    """A logical name resolved to an Appium strategy and value."""

    name: str
    strategy: str
    value: str
    params: tuple[tuple[str, str], ...] = ()

    @property
    def by(self) -> str:
        return STRATEGIES[self.strategy]

    @property
    def key(self) -> str:
        """Stable identity for fingerprints and heals: the logical name plus its parameters."""
        if not self.params:
            return self.name
        return self.name + "[" + ",".join(f"{k}={v}" for k, v in self.params) + "]"

    def replaced(self, strategy: str, value: str) -> "Locator":
        """The same logical locator pointed at a different element, as a heal does."""
        return Locator(self.name, strategy, value, self.params)

    def __str__(self) -> str:
        return f"L({self.name!r}) [{self.strategy}={self.value}]"


class LocatorMap:
    """Logical names mapped to templates such as ``("id", "item.row.{name}")``."""

    def __init__(self, entries: Mapping[str, tuple[str, str]]) -> None:
        self._entries = dict(entries)

    @classmethod
    def from_yaml(cls, path: Path) -> "LocatorMap":
        """Parse a file where each value is a bare accessibility id or ``{strategy: value}``."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise LocatorError(f"{path}: expected a mapping of logical names")
        entries: dict[str, tuple[str, str]] = {}
        for name, spec in raw.items():
            if isinstance(spec, str):
                entries[str(name)] = ("id", spec)
            elif isinstance(spec, dict) and len(spec) == 1:
                ((strategy, value),) = spec.items()
                if strategy not in STRATEGIES or not isinstance(value, str):
                    raise LocatorError(f"{path}: {name}: unknown strategy {strategy!r}")
                entries[str(name)] = (strategy, value)
            else:
                raise LocatorError(f"{path}: {name}: expected a string or one {{strategy: value}}")
        return cls(entries)

    def get(self, name: str, /, **params: str) -> Locator:
        try:
            strategy, template = self._entries[name]
        except KeyError:
            raise LocatorError(f"unknown locator {name!r}") from None
        try:
            value = template.format(**params)
        except KeyError as missing:
            raise LocatorError(f"locator {name!r} needs parameter {missing}") from None
        return Locator(name, strategy, value, tuple(sorted(params.items())))

    def __contains__(self, name: object) -> bool:
        return name in self._entries


_active: LocatorMap | None = None


def use_locators(locators: LocatorMap | None) -> None:
    """Set the map that L() resolves against; the pytest plugin calls this per session."""
    global _active
    _active = locators


def L(name: str, /, **params: str) -> Locator:
    """Resolve a logical name, filling ``{placeholders}`` from keyword arguments."""
    if _active is None:
        raise LocatorError("no locator file loaded; set healorhalt_locators in pytest config")
    return _active.get(name, **params)
