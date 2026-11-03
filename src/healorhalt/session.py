"""Appium session and xcrun simctl helpers."""

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from appium import webdriver
from appium.options.ios import XCUITestOptions

APPIUM_URL = "http://127.0.0.1:4723"


def boot_simulator(device: str) -> None:
    """Boot the simulator if needed and block until it has finished booting."""
    subprocess.run(["xcrun", "simctl", "bootstatus", device, "-b"], check=True, capture_output=True)
    subprocess.run(["open", "-a", "Simulator"], check=True)


@contextmanager
def ios_session(device: str, bundle_id: str, url: str = APPIUM_URL) -> Iterator[webdriver.Remote]:
    """Start an XCUITest session on a booted simulator and quit it on exit."""
    options = XCUITestOptions()
    options.device_name = device
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
