"""Appium session and xcrun simctl helpers."""

import contextlib
import json
import re
import subprocess
import time
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from appium import webdriver
from appium.options.ios import XCUITestOptions
from appium.webdriver.common.appiumby import AppiumBy
from appium.webdriver.webelement import WebElement
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

from healorhalt import evidence
from healorhalt.evidence import Evidence, Target
from healorhalt.fingerprint import parse, rect_of
from healorhalt.heal import Heal
from healorhalt.locator import L, Locator
from healorhalt.verdict import (
    HALT,
    Check,
    HealHalted,
    Verdict,
    check_effect,
    check_enabled,
    check_replay,
    check_screen,
    decide,
    screen_of,
)

if TYPE_CHECKING:
    from healorhalt.fingerprint import Recorder
    from healorhalt.heal import Healer

APPIUM_URL = "http://127.0.0.1:4723"
_UDID = re.compile(r"^[0-9A-F]{8}-([0-9A-F]{4}-){3}[0-9A-F]{12}$", re.IGNORECASE)


def resolve_udid(device: str, os_version: str | None = None) -> str:
    """Return the UDID of the available simulator named `device`, on `os_version` if given.

    Several runtimes can each have an "iPhone 17", so pinning the version keeps runs on the
    same iOS build. A UDID passed as `device` is returned unchanged.
    """
    if _UDID.match(device):
        return device
    listing = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "available", "-j"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    suffix = "iOS-" + os_version.replace(".", "-") if os_version else "iOS-"
    for runtime, devices in sorted(json.loads(listing)["devices"].items(), reverse=True):
        if runtime.endswith(suffix) if os_version else suffix in runtime:
            for entry in devices:
                if entry["name"] == device:
                    return str(entry["udid"])
    raise LookupError(f"no available simulator named {device!r} on iOS {os_version or 'any'}")


def boot_simulator(device: str) -> None:
    """Boot the simulator if needed and block until it has finished booting."""
    subprocess.run(["xcrun", "simctl", "bootstatus", device, "-b"], check=True, capture_output=True)
    subprocess.run(["open", "-a", "Simulator"], check=True)


@contextmanager
def ios_session(
    udid: str, bundle_id: str, app: Path | None = None, url: str = APPIUM_URL
) -> Iterator[webdriver.Remote]:
    """Start an XCUITest session on a booted simulator and quit it on exit.

    With `app`, that build is installed first. simctl does the install because with no_reset
    Appium keeps whatever build is already on the simulator.
    """
    if app is not None:
        subprocess.run(["xcrun", "simctl", "install", udid, str(app)], check=True)
    options = XCUITestOptions()
    options.udid = udid
    options.bundle_id = bundle_id
    options.no_reset = True
    options.new_command_timeout = 120
    driver = webdriver.Remote(url, options=options)
    try:
        yield driver
    finally:
        driver.quit()


def capture(driver: webdriver.Remote, out_dir: Path, name: str) -> tuple[Path, Path]:
    """Save a screenshot and the accessibility XML; return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"{name}.png"
    xml = out_dir / f"{name}.xml"
    driver.get_screenshot_as_file(str(png))
    xml.write_text(driver.page_source, encoding="utf-8")
    return png, xml


@dataclass
class PendingHeal:
    """A healed step under the verdict engine, with its checks and evidence so far."""

    heal: "Heal"
    evidence: Evidence
    checks: list[Check]
    verdict: Verdict | None = None
    card: Path | None = None


class App:
    """The app under test, driven through logical locators.

    Every element lookup goes through `find`. With a recorder, each element found is
    fingerprinted and each step's resulting screen is saved; with a healer, a locator that finds
    nothing is handed to the healing tiers. In full mode a healed step is judged by the verdict
    engine: checks before the action, checks after it, and a last one when the test ends
    (`finish`). A HALT stops the test with HealHalted and an evidence card.
    """

    def __init__(
        self,
        driver: webdriver.Remote,
        bundle_id: str,
        timeout: float = 5.0,
        healer: "Healer | None" = None,
        recorder: "Recorder | None" = None,
        screens: Mapping[str, str] | None = None,
        evidence_dir: Path = Path("artifacts/evidence"),
    ) -> None:
        self.driver = driver
        self.bundle_id = bundle_id
        self.timeout = timeout
        self.healer = healer
        self.recorder = recorder
        self.screens = dict(screens or {})
        """Screen names mapped to the identifier that marks each screen (``screen.*`` locators)."""
        self.evidence_dir = evidence_dir
        self.verdicts: list[PendingHeal] = []
        """This test's judged heals, in order."""
        self._steps: Counter[str] = Counter()
        self._heal: Heal | None = None

    def launch(self, mutation: str | None = None) -> None:
        """Relaunch from a clean state, with `-mutation <ID>` if given."""
        self.driver.execute_script("mobile: terminateApp", {"bundleId": self.bundle_id})
        arguments = ["-mutation", mutation] if mutation else []
        self.driver.execute_script(
            "mobile: launchApp", {"bundleId": self.bundle_id, "arguments": arguments}
        )

    def _wait_find(self, locator: Locator, timeout: float) -> WebElement:
        wait = WebDriverWait(self.driver, timeout, poll_frequency=0.25)
        try:
            element: WebElement = wait.until(lambda d: d.find_element(locator.by, locator.value))
        except TimeoutException as e:
            raise NoSuchElementException(f"{locator} not found") from e
        return element

    def _try_find(self, locator: Locator) -> WebElement | None:
        try:
            return self._wait_find(locator, 1.0)
        except NoSuchElementException:
            return None

    def _count(self, locator: Locator) -> int:
        return len(self.driver.find_elements(locator.by, locator.value))

    def find(
        self,
        locator: Locator,
        timeout: float | None = None,
        heal: bool = True,
        action: str = "find",
    ) -> WebElement:
        """Find an element, healing the locator if it finds nothing and a healer is set.

        `action` is what the step does with the element, in words for the LLM tier ("tap").
        """
        self._heal = None
        try:
            element = self._wait_find(locator, self.timeout if timeout is None else timeout)
        except NoSuchElementException:
            if not heal or self.healer is None:
                raise
            healed = self.healer.heal(
                locator,
                self.driver.page_source,
                self._try_find,
                screenshot=self.driver.get_screenshot_as_png,
                action=action,
                count=self._count,
            )
            if healed is None:
                if self.healer.verifies:
                    self._halt_missing(locator, action)
                raise
            self._heal = healed
            return healed.element
        if self.recorder is not None:
            self.recorder.record(locator, element)
        return element

    def find_all(self, locator: Locator) -> list[WebElement]:
        return list(self.driver.find_elements(locator.by, locator.value))

    def exists(self, locator: Locator, timeout: float = 0) -> bool:
        """Whether the locator matches, without healing: absence is the answer, not a fault."""
        try:
            self.find(locator, timeout, heal=False)
        except NoSuchElementException:
            return False
        return True

    def wait_gone(self, locator: Locator, timeout: float | None = None) -> None:
        WebDriverWait(self.driver, self.timeout if timeout is None else timeout, 0.25).until(
            lambda d: not d.find_elements(locator.by, locator.value),
            message=f"{locator} still present",
        )

    # Steps. Each finds its element, then `_before` judges a healed one (full mode), the action
    # runs, and `_after` records or checks the screen it led to.

    def tap(self, locator: Locator) -> None:
        """Tap an element.

        XCUITest silently ignores a click on an element it reports as not hittable, such as a
        button wrapped in a narrower container, even when a person could tap it; tap its frame's
        centre instead.
        """
        element = self.find(locator, action="tap")
        pending = self._before(locator, "tap", element)
        if element.get_attribute("hittable") == "false":
            rect = element.rect
            self.driver.execute_script(
                "mobile: tap",
                {"x": rect["x"] + rect["width"] / 2, "y": rect["y"] + rect["height"] / 2},
            )
        else:
            element.click()
        self._after(locator, "tap", pending)

    def toggle(self, locator: Locator) -> None:
        """Flip a switch and wait for its value to change.

        A SwiftUI Toggle is a full-width row whose centre is the label, and tapping it does
        nothing; the control is a child switch at the trailing edge, so tap that.
        """
        element = self.find(locator, action="toggle")
        pending = self._before(locator, "toggle", element)
        before = _attr(element, "value")
        inner = element.find_elements(AppiumBy.IOS_CLASS_CHAIN, "**/XCUIElementTypeSwitch")
        (inner[0] if inner else element).click()
        try:
            WebDriverWait(self.driver, self.timeout, 0.25).until(
                lambda _: element.get_attribute("value") != before,
                message=f"{locator} didn't change from {before!r}",
            )
        except TimeoutException:
            if pending is None:
                raise
        effect = None
        if pending is not None:
            now = _attr(element, "value")
            effect = check_effect("toggle", pending.evidence.target_type, before, now)
        self._after(locator, "toggle", pending, effect)

    def type(self, locator: Locator, text: str) -> None:
        element = self.find(locator, action="type text into")
        pending = self._before(locator, "type text into", element)
        element.clear()
        element.send_keys(text)
        effect = None
        if pending is not None:
            pending.evidence.typed = text
            now = _attr(element, "value")
            effect = check_effect("type text into", pending.evidence.target_type, text, now)
        self._after(locator, "type text into", pending, effect)

    def _read(self, locator: Locator, action: str, attribute: str) -> str:
        element = self.find(locator, action=action)
        pending = self._before(locator, action, element)
        result = str(element.get_attribute(attribute) or "")
        self._after(locator, action, pending)
        return result

    def label(self, locator: Locator) -> str:
        return self._read(locator, "read the label of", "label")

    def value(self, locator: Locator) -> str:
        return self._read(locator, "read the value of", "value")

    def is_enabled(self, locator: Locator) -> bool:
        element = self.find(locator, action="check")
        pending = self._before(locator, "check", element)
        enabled = bool(element.is_enabled())
        self._after(locator, "check", pending)
        return enabled

    # The verdict engine's view of a step.

    def _before(self, locator: Locator, action: str, element: WebElement) -> PendingHeal | None:
        """Start judging the step if its element was healed: round 1 checks and the screen
        before the action. HALT here stops the step before it acts."""
        heal, self._heal = self._heal, None
        if heal is None or self.healer is None or not self.healer.verifies:
            return None
        checks = [*heal.checks, check_enabled(action, bool(element.is_enabled()))]
        evidence = Evidence(
            key=locator.key,
            action=action,
            window_width=int(self.driver.get_window_size()["width"]),
            fingerprint=heal.fingerprint,
            target=_target(heal, element),
            before=self.healer.store.fingerprint_screenshot(self.bundle_id, locator.key),
            after=self.driver.get_screenshot_as_png(),
            data={
                "heal_id": heal.heal_id,
                "tier": heal.tier,
                "score": round(heal.score, 3),
                "old_locator": f"{locator.strategy}={locator.value}",
                "new_locator": f"{heal.locator.strategy}={heal.locator.value}",
                "test": self.healer.test,
                "mutation": self.healer.mutation,
                **heal.data,
            },
        )
        pending = PendingHeal(heal, evidence, checks)
        verdict = decide(checks)
        if verdict.decision == HALT:
            self._judge(pending, verdict)
        return pending

    def _after(
        self,
        locator: Locator,
        action: str,
        pending: PendingHeal | None,
        effect: Check | None = None,
    ) -> None:
        """Record the screen the step led to (with a recorder), or check it (a healed step)."""
        self._steps[f"{locator.key}|{action}"] += 1
        step = f"{locator.key}|{action}|{self._steps[f'{locator.key}|{action}']}"
        if self.recorder is None and pending is None:
            return
        self.wait_settled()
        screen = screen_of(parse(self.driver.page_source), self.screens)
        if self.recorder is not None:
            self.recorder.record_screen(step, screen)
        if pending is None or self.healer is None:
            return
        expected = self.healer.store.screen(self.bundle_id, self.healer.test, step)
        pending.evidence.result = self.driver.get_screenshot_as_png()
        pending.evidence.expected_screen = expected
        pending.evidence.actual_screen = screen
        pending.checks += [
            effect or check_effect(action, pending.evidence.target_type, None, None),
            check_screen(expected, screen, self.healer.verdict),
        ]
        self._judge(pending, decide(pending.checks))

    def _judge(self, pending: PendingHeal, verdict: Verdict) -> None:
        """Save the verdict and its card; raise HealHalted on HALT."""
        assert self.healer is not None
        heal_id = pending.heal.heal_id
        safe_key = re.sub(r"[^\w.-]+", "_", pending.evidence.key)
        out = self.evidence_dir / self.healer.run_id / f"{heal_id}-{safe_key}"
        pending.verdict = verdict
        pending.card = evidence.save(pending.evidence, verdict, out)
        self.healer.store.set_verdict(heal_id, verdict, str(pending.card))
        if pending not in self.verdicts:
            self.verdicts.append(pending)
        if verdict.decision == HALT:
            raise HealHalted(
                f"HALT (likely real bug): {evidence.describe(pending.evidence, verdict)}\n"
                f"evidence: {pending.card}"
            )

    def _halt_missing(self, locator: Locator, action: str) -> None:
        """No tier found the element: HALT, with a card showing where it used to be."""
        assert self.healer is not None and self.healer.miss is not None
        miss = self.healer.miss
        fake = Heal(cast(WebElement, None), locator, miss.heal_id, -1, 0.0, miss.fingerprint)
        pending = PendingHeal(
            fake,
            Evidence(
                key=locator.key,
                action=action,
                window_width=int(self.driver.get_window_size()["width"]),
                fingerprint=miss.fingerprint,
                before=self.healer.store.fingerprint_screenshot(self.bundle_id, locator.key),
                after=self.driver.get_screenshot_as_png(),
                data={"heal_id": miss.heal_id, "test": self.healer.test},
            ),
            [Check("found", False, miss.reason)],
        )
        self._judge(pending, decide(pending.checks))

    def finish(self, passed: bool, failure: str = "", halted: bool = False) -> list[PendingHeal]:
        """Round 3, when the test ends: a heal followed by a failing test becomes HALT.

        If the test stopped at a later HALT (`halted`), earlier heals can't be judged by the
        outcome, so they wait for review instead. Returns this test's judged heals.
        """
        for pending in self.verdicts:
            if pending.verdict is None or pending.verdict.decision == HALT:
                continue
            if halted:
                replay = Check("replay", None, "the test stopped at a later HALT", hard=False)
            else:
                replay = check_replay(passed, failure)
            pending.checks.append(replay)
            with contextlib.suppress(HealHalted):
                self._judge(pending, decide(pending.checks))
        return self.verdicts

    def wait_for_screen(self, screen: str, timeout: float | None = None) -> None:
        """Wait until the screen's marker (``screen.<screen>``) is visible and the UI has settled.

        Visibility matters because a screen under a sheet stays in the tree, hidden. Settling
        matters because taps sent during a transition are dropped, so this also waits until
        the page source stops changing.
        """
        locator = L(f"screen.{screen}")
        wait = WebDriverWait(self.driver, self.timeout if timeout is None else timeout, 0.25)
        try:
            wait.until(
                lambda d: any(e.is_displayed() for e in d.find_elements(locator.by, locator.value))
            )
        except TimeoutException as e:
            raise NoSuchElementException(f"screen {screen!r}: {locator} not visible") from e
        self.wait_settled()

    def wait_settled(self, timeout: float = 5.0) -> None:
        """Wait until two page-source snapshots in a row match, i.e. animations have finished."""
        previous = self.driver.page_source
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.3)
            current = self.driver.page_source
            if current == previous:
                return
            previous = current


def _target(heal: "Heal", element: WebElement) -> Target:
    """The healed element as it is now, from the page source if the healer saw it there."""
    if heal.node is not None:
        node = heal.node
        return Target(str(node.tag), node.get("name", ""), node.get("label", ""), rect_of(node))
    r = element.rect
    return Target(
        str(element.get_attribute("type") or ""),
        str(element.get_attribute("name") or ""),
        str(element.get_attribute("label") or ""),
        (int(r["x"]), int(r["y"]), int(r["width"]), int(r["height"])),
    )


def _attr(element: WebElement, name: str) -> str | None:
    value = element.get_attribute(name)
    return None if value is None else str(value)
