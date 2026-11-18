"""Captures element fingerprints on green runs."""

import io
import json
import math
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from appium import webdriver
from appium.webdriver.webelement import WebElement
from lxml import etree
from PIL import Image

from healorhalt.locator import Locator

if TYPE_CHECKING:
    from healorhalt.store import Store
    from healorhalt.verdict import Screen

Node = etree._Element
Rect = tuple[int, int, int, int]

# Kept as candidates even when not marked accessible, since the real control often isn't
# (a SwiftUI Toggle's inner switch, for one).
INTERACTIVE = {
    "XCUIElementTypeButton",
    "XCUIElementTypeCell",
    "XCUIElementTypeLink",
    "XCUIElementTypeSearchField",
    "XCUIElementTypeSecureTextField",
    "XCUIElementTypeSwitch",
    "XCUIElementTypeTextField",
}
ANCESTOR_DEPTH = 6
NEIGHBOR_COUNT = 4


@dataclass(frozen=True)
class Fingerprint:
    """What an element looked like on a green run: the evidence a heal is scored against."""

    key: str
    type: str
    name: str
    label: str
    value: str
    rect: Rect
    ancestors: tuple[str, ...]
    """Nearest-first ``Type:name`` of the element's ancestors, up to ANCESTOR_DEPTH."""
    neighbors: tuple[str, ...]
    """Labels of the nearest other visible, labelled elements on screen."""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "Fingerprint":
        data = json.loads(text)
        return cls(
            key=data["key"],
            type=data["type"],
            name=data["name"],
            label=data["label"],
            value=data["value"],
            rect=(data["rect"][0], data["rect"][1], data["rect"][2], data["rect"][3]),
            ancestors=tuple(data["ancestors"]),
            neighbors=tuple(data["neighbors"]),
        )


def parse(page_source: str) -> Node:
    return etree.fromstring(page_source.encode("utf-8"))


def rect_of(node: Node) -> Rect:
    return (
        int(node.get("x", "0")),
        int(node.get("y", "0")),
        int(node.get("width", "0")),
        int(node.get("height", "0")),
    )


def center(rect: Rect) -> tuple[float, float]:
    x, y, width, height = rect
    return x + width / 2, y + height / 2


def distance(a: Rect, b: Rect) -> float:
    (ax, ay), (bx, by) = center(a), center(b)
    return math.hypot(ax - bx, ay - by)


def short_type(node: Node) -> str:
    return str(node.tag).removeprefix("XCUIElementType")


def is_visible(node: Node) -> bool:
    return node.get("visible") == "true"


def candidates(root: Node) -> list[Node]:
    """Elements a locator could plausibly mean: accessible or interactive.

    Hidden elements are kept, because XCUITest marks some on-screen elements visible="false"
    (a button wrapped in a narrower container, for one); scoring penalizes them instead.
    """
    return [
        node
        for node in root.iter()
        if isinstance(node.tag, str)
        and node.tag.startswith("XCUIElementType")
        and node.tag != "XCUIElementTypeApplication"
        and (node.get("accessible") == "true" or node.tag in INTERACTIVE)
    ]


def ancestors_of(node: Node) -> tuple[str, ...]:
    chain: list[str] = []
    parent = node.getparent()
    while parent is not None and len(chain) < ANCESTOR_DEPTH:
        if parent.tag in ("AppiumAUT", "XCUIElementTypeApplication", "XCUIElementTypeWindow"):
            break
        chain.append(f"{short_type(parent)}:{parent.get('name', '')}")
        parent = parent.getparent()
    return tuple(chain)


def neighbors_of(node: Node, root: Node) -> tuple[str, ...]:
    """Labels of the nearest labelled elements, skipping the element's own subtree and ancestors."""
    own = set(node.iter()) | set(node.iterancestors())
    rect = rect_of(node)
    labelled = [
        other
        for other in candidates(root)
        if is_visible(other)
        and other not in own
        and other.get("label")
        and other.get("label") != node.get("label")
    ]
    labelled.sort(key=lambda other: distance(rect, rect_of(other)))
    labels: list[str] = []
    for other in labelled:
        label = str(other.get("label"))
        if label not in labels:
            labels.append(label)
        if len(labels) == NEIGHBOR_COUNT:
            break
    return tuple(labels)


def fingerprint(key: str, node: Node, root: Node) -> Fingerprint:
    return Fingerprint(
        key=key,
        type=str(node.tag),
        name=node.get("name", ""),
        label=node.get("label", ""),
        value=node.get("value", ""),
        rect=rect_of(node),
        ancestors=ancestors_of(node),
        neighbors=neighbors_of(node, root),
    )


def node_for(root: Node, element_type: str, rect: Rect) -> Node | None:
    """The tree node for an element found by the driver, matched on type and frame."""
    for node in root.iter(element_type):
        if rect_of(node) == rect:
            return innermost(node)
    return None


def innermost(node: Node) -> Node:
    """The control itself when `node` only wraps it.

    iOS 26 wraps each nav-bar button in an Other with the same identifier and frame, and an
    accessibility-id lookup returns the wrapper first. Fingerprinting the wrapper would record
    the wrong type, so prefer an accessible descendant with the same name and frame.
    """
    for child in node.iterdescendants():
        if (
            child.get("name") == node.get("name")
            and rect_of(child) == rect_of(node)
            and child.get("accessible") == "true"
        ):
            return child
    return node


def crop(png: bytes, rect: Rect, window_width: int) -> bytes:
    """Cut an element out of a screenshot; rects are in points and screenshots in pixels."""
    image = Image.open(io.BytesIO(png))
    scale = image.width / window_width
    x, y, width, height = rect
    box = (
        round(x * scale),
        round(y * scale),
        round((x + width) * scale),
        round((y + height) * scale),
    )
    out = io.BytesIO()
    image.crop(box).save(out, format="PNG")
    return out.getvalue()


class Recorder:
    """Collects fingerprints and screens during a test; the plugin saves them only if it passes.

    Each fingerprint keeps the whole screenshot, so evidence cards can show the element where it
    was. Each step also records the screen it led to, for the verdict engine's next-screen check.
    """

    def __init__(self, driver: webdriver.Remote) -> None:
        self.driver = driver
        self.pending: dict[str, tuple[Fingerprint, bytes]] = {}
        self.screens: dict[str, Screen] = {}

    def record(self, locator: Locator, element: WebElement) -> None:
        if locator.key in self.pending:
            return
        root = parse(self.driver.page_source)
        size = element.rect
        rect = (int(size["x"]), int(size["y"]), int(size["width"]), int(size["height"]))
        node = node_for(root, str(element.get_attribute("type")), rect)
        if node is None:
            return
        self.pending[locator.key] = (
            fingerprint(locator.key, node, root),
            self.driver.get_screenshot_as_png(),
        )

    def record_screen(self, step: str, screen: "Screen") -> None:
        self.screens[step] = screen

    def flush(self, store: "Store", app_id: str, test: str) -> int:
        for fp, shot in self.pending.values():
            store.save_fingerprint(app_id, fp, shot, test)
        for step, screen in self.screens.items():
            store.save_screen(app_id, test, step, screen)
        count = len(self.pending)
        self.pending.clear()
        self.screens.clear()
        return count
