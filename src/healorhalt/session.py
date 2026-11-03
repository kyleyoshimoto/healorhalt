"""Appium session and xcrun simctl helpers."""

import json
import re
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from appium import webdriver
from appium.options.ios import XCUITestOptions
from appium.webdriver.common.appiumby import AppiumBy
from appium.webdriver.webelement import WebElement
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

from healorhalt.locator import L, Locator

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

    With `app`, that build is installed first.
    """
    options = XCUITestOptions()
    options.udid = udid
    options.bundle_id = bundle_id
    if app is not None:
        options.app = str(app)
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


class App:
    """The app under test, driven through logical locators.

    Every element lookup goes through `find`, which is where healing will hook in.
    """

    def __init__(self, driver: webdriver.Remote, bundle_id: str, timeout: float = 5.0) -> None:
        self.driver = driver
        self.bundle_id = bundle_id
        self.timeout = timeout

    def launch(self, mutation: str | None = None) -> None:
        """Relaunch from a clean state, with `-mutation <ID>` if given."""
        self.driver.execute_script("mobile: terminateApp", {"bundleId": self.bundle_id})
        arguments = ["-mutation", mutation] if mutation else []
        self.driver.execute_script(
            "mobile: launchApp", {"bundleId": self.bundle_id, "arguments": arguments}
        )

    def find(self, locator: Locator, timeout: float | None = None) -> WebElement:
        wait = WebDriverWait(
            self.driver, self.timeout if timeout is None else timeout, poll_frequency=0.25
        )
        try:
            element: WebElement = wait.until(lambda d: d.find_element(locator.by, locator.value))
        except TimeoutException as e:
            raise NoSuchElementException(f"{locator} not found") from e
        return element

    def find_all(self, locator: Locator) -> list[WebElement]:
        return list(self.driver.find_elements(locator.by, locator.value))

    def exists(self, locator: Locator, timeout: float = 0) -> bool:
        try:
            self.find(locator, timeout)
        except NoSuchElementException:
            return False
        return True

    def wait_gone(self, locator: Locator, timeout: float | None = None) -> None:
        WebDriverWait(self.driver, self.timeout if timeout is None else timeout, 0.25).until(
            lambda d: not d.find_elements(locator.by, locator.value),
            message=f"{locator} still present",
        )

    def tap(self, locator: Locator) -> None:
        self.find(locator).click()

    def toggle(self, locator: Locator) -> None:
        """Flip a switch and wait for its value to change.

        A SwiftUI Toggle is a full-width row whose centre is the label, and tapping it does
        nothing; the control is a child switch at the trailing edge, so tap that.
        """
        element = self.find(locator)
        before = element.get_attribute("value")
        inner = element.find_elements(AppiumBy.IOS_CLASS_CHAIN, "**/XCUIElementTypeSwitch")
        (inner[0] if inner else element).click()
        WebDriverWait(self.driver, self.timeout, 0.25).until(
            lambda _: element.get_attribute("value") != before,
            message=f"{locator} didn't change from {before!r}",
        )

    def type(self, locator: Locator, text: str) -> None:
        element = self.find(locator)
        element.clear()
        element.send_keys(text)

    def label(self, locator: Locator) -> str:
        return str(self.find(locator).get_attribute("label") or "")

    def value(self, locator: Locator) -> str:
        return str(self.find(locator).get_attribute("value") or "")

    def is_enabled(self, locator: Locator) -> bool:
        return bool(self.find(locator).is_enabled())

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
