"""pytest plugin: fixtures and CLI flags."""

import os
import urllib.request
from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from appium import webdriver

from healorhalt.locator import LocatorMap, use_locators
from healorhalt.session import (
    APPIUM_URL,
    App,
    boot_simulator,
    capture,
    ios_session,
    resolve_udid,
)

FAILURES_DIR = Path("artifacts/failures")
HEAL_MODES = ("off", "cache", "heuristic", "llm", "full")


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("healorhalt")
    group.addoption(
        "--heal", choices=HEAL_MODES, default="off", help="Healing tiers to use (default: off)"
    )
    group.addoption("--mutation", default=None, help="Launch the app with -mutation <ID>")
    group.addoption(
        "--sim", default=os.environ.get("SIM", "iPhone 17"), help="Simulator name or UDID"
    )
    group.addoption(
        "--sim-os", default=os.environ.get("SIM_OS", "26.4"), help="iOS version of the simulator"
    )
    group.addoption("--appium-url", default=APPIUM_URL, help="Appium server URL")
    parser.addini("healorhalt_locators", "YAML file of logical locators, relative to rootdir")
    parser.addini("healorhalt_bundle_id", "Bundle ID of the app under test")
    parser.addini("healorhalt_app", "Path to the built .app, relative to rootdir")


def pytest_configure(config: pytest.Config) -> None:
    heal = config.getoption("--heal")
    if heal != "off":
        raise pytest.UsageError(f"--heal={heal} isn't implemented yet; only --heal=off works")


def _ini_path(config: pytest.Config, name: str) -> Path:
    value = str(config.getini(name) or "")
    if not value:
        raise pytest.UsageError(f"set {name} in your pytest config")
    return config.rootpath / value


@pytest.fixture(scope="session")
def locators(pytestconfig: pytest.Config) -> Iterator[LocatorMap]:
    """The session's locator map, also made active for L()."""
    locator_map = LocatorMap.from_yaml(_ini_path(pytestconfig, "healorhalt_locators"))
    use_locators(locator_map)
    yield locator_map
    use_locators(None)


@pytest.fixture(scope="session")
def driver(pytestconfig: pytest.Config) -> Iterator[webdriver.Remote]:
    """One Appium session for the whole run, with the app installed."""
    url = str(pytestconfig.getoption("--appium-url"))
    try:
        urllib.request.urlopen(f"{url}/status", timeout=3)
    except OSError:
        pytest.exit(f"Appium isn't reachable at {url}; start it with `appium`", returncode=3)
    app_path = _ini_path(pytestconfig, "healorhalt_app")
    if not app_path.exists():
        pytest.exit(f"{app_path} doesn't exist; build it with `make app`", returncode=3)
    udid = resolve_udid(pytestconfig.getoption("--sim"), pytestconfig.getoption("--sim-os"))
    boot_simulator(udid)
    bundle_id = str(pytestconfig.getini("healorhalt_bundle_id"))
    with ios_session(udid, bundle_id, app=app_path, url=url) as session:
        yield session


@pytest.fixture
def app(driver: webdriver.Remote, locators: LocatorMap, pytestconfig: pytest.Config) -> App:
    """The app relaunched from a clean state for each test."""
    app = App(driver, str(pytestconfig.getini("healorhalt_bundle_id")))
    app.launch(mutation=pytestconfig.getoption("--mutation"))
    return app


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Save a screenshot and the page source when a UI test fails."""
    report = yield
    app = getattr(item, "funcargs", {}).get("app")
    if report.failed and report.when == "call" and isinstance(app, App):
        name = item.nodeid.replace("/", "_").replace("::", "__")
        png, _ = capture(app.driver, item.config.rootpath / FAILURES_DIR, name)
        report.sections.append(("healorhalt", f"screenshot and page source saved next to {png}"))
    return report
