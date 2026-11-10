"""pytest plugin: fixtures and CLI flags."""

import os
import urllib.request
import uuid
from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from appium import webdriver

from healorhalt.fingerprint import Recorder
from healorhalt.heal import DEFAULT_THRESHOLD, Healer
from healorhalt.locator import LocatorMap, use_locators
from healorhalt.session import (
    APPIUM_URL,
    App,
    boot_simulator,
    capture,
    ios_session,
    resolve_udid,
)
from healorhalt.store import Store

FAILURES_DIR = Path("artifacts/failures")
HEAL_MODES = ("off", "cache", "heuristic", "llm", "full")
IMPLEMENTED_HEAL_MODES = ("off", "cache", "heuristic")
RUN_ID = pytest.StashKey[str]()


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
    group.addoption(
        "--record",
        action="store_true",
        help="Save fingerprints of every element found by tests that pass",
    )
    parser.addini("healorhalt_locators", "YAML file of logical locators, relative to rootdir")
    parser.addini("healorhalt_bundle_id", "Bundle ID of the app under test")
    parser.addini("healorhalt_app", "Path to the built .app, relative to rootdir")
    parser.addini(
        "healorhalt_db",
        "SQLite file for fingerprints and heals",
        default=".healorhalt/healorhalt.db",
    )
    parser.addini(
        "healorhalt_heal_threshold",
        "Minimum Tier 1 score for a heal",
        default=str(DEFAULT_THRESHOLD),
    )


def pytest_configure(config: pytest.Config) -> None:
    heal = config.getoption("--heal")
    if heal not in IMPLEMENTED_HEAL_MODES:
        raise pytest.UsageError(
            f"--heal={heal} isn't implemented yet; use one of {', '.join(IMPLEMENTED_HEAL_MODES)}"
        )
    if heal != "off" and config.getoption("--record"):
        raise pytest.UsageError("--record needs --heal=off, so fingerprints come from real matches")
    config.stash[RUN_ID] = uuid.uuid4().hex[:12]


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


@pytest.fixture(scope="session")
def store(pytestconfig: pytest.Config) -> Iterator[Store]:
    """The fingerprint and heal database."""
    db = Store(pytestconfig.rootpath / str(pytestconfig.getini("healorhalt_db")))
    yield db
    db.close()


@pytest.fixture
def app(
    request: pytest.FixtureRequest,
    driver: webdriver.Remote,
    locators: LocatorMap,
    store: Store,
    pytestconfig: pytest.Config,
) -> App:
    """The app relaunched from a clean state for each test, healing per --heal."""
    bundle_id = str(pytestconfig.getini("healorhalt_bundle_id"))
    mutation = pytestconfig.getoption("--mutation")
    heal = pytestconfig.getoption("--heal")
    healer = None
    if heal != "off":
        healer = Healer(
            store,
            bundle_id,
            heal,
            run_id=pytestconfig.stash[RUN_ID],
            test=request.node.nodeid,
            mutation=mutation,
            threshold=float(pytestconfig.getini("healorhalt_heal_threshold")),
        )
    recorder = Recorder(driver) if pytestconfig.getoption("--record") else None
    app = App(driver, bundle_id, healer=healer, recorder=recorder)
    app.launch(mutation=mutation)
    return app


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Save fingerprints when a test passes, or a screenshot and page source when it fails."""
    report = yield
    funcargs = getattr(item, "funcargs", {})
    app, store = funcargs.get("app"), funcargs.get("store")
    if report.when != "call" or not isinstance(app, App):
        return report
    if report.failed:
        name = item.nodeid.replace("/", "_").replace("::", "__")
        png, _ = capture(app.driver, item.config.rootpath / FAILURES_DIR, name)
        report.sections.append(("healorhalt", f"screenshot and page source saved next to {png}"))
    elif app.recorder is not None and isinstance(store, Store):
        app.recorder.flush(store, app.bundle_id, item.nodeid)
    return report


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """List this run's heal attempts with their scores."""
    config = terminalreporter.config
    if config.getoption("--heal") == "off":
        return
    store = Store(config.rootpath / str(config.getini("healorhalt_db")))
    try:
        heals = store.heals(run_id=config.stash[RUN_ID])
    finally:
        store.close()
    terminalreporter.section("healorhalt heals")
    if not heals:
        terminalreporter.write_line("no locator needed healing")
    for heal in heals:
        score = f"{heal['score']:.2f}" if heal["score"] is not None else "-"
        new = f"{heal['new_strategy']}={heal['new_value']}" if heal["new_strategy"] else "-"
        terminalreporter.write_line(
            f"#{heal['id']} {heal['outcome']:<15} tier {heal['tier']} score {score}  "
            f"{heal['key']}: {heal['old_strategy']}={heal['old_value']} -> {new}"
        )
