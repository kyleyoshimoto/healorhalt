"""pytest plugin: fixtures and CLI flags."""

import os
import urllib.request
import uuid
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from appium import webdriver

from healorhalt.evidence import locator_patch
from healorhalt.fingerprint import Recorder
from healorhalt.heal import DEFAULT_THRESHOLD, LLM_MIN_CONFIDENCE, Healer
from healorhalt.llm.ollama_client import DEFAULT_MODEL
from healorhalt.locator import LocatorMap, use_locators
from healorhalt.session import (
    APPIUM_URL,
    App,
    PendingHeal,
    boot_simulator,
    capture,
    ios_session,
    resolve_udid,
)
from healorhalt.store import Store
from healorhalt.verdict import ESCALATE, HealHalted, VerdictConfig

FAILURES_DIR = Path("artifacts/failures")
EVIDENCE_DIR = Path("artifacts/evidence")
HEAL_MODES = ("off", "cache", "heuristic", "llm", "full")
RUN_ID = pytest.StashKey[str]()
VERDICTS = "healorhalt_verdicts"
"""user_properties key holding a test's verdicts as (decision, key, card) tuples."""
VERDICT_INI = {
    "heal_threshold": "Least combined score for HEAL (full mode)",
    "floor": "Combined score below which a heal is HALT (full mode)",
    "llm_weight": "Share of a Tier 2 combined score taken from the LLM's confidence",
    "ambiguity_margin": "A Tier 1 runner-up this close to the best makes a heal ESCALATE",
    "screen_similarity": "Least overlap of visible ids for the next screen to count as the same",
}


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
    parser.addini("healorhalt_llm_model", "Ollama model for Tier 2", default=DEFAULT_MODEL)
    parser.addini(
        "healorhalt_llm_timeout", "Seconds before a Tier 2 call is abandoned", default="30"
    )
    parser.addini(
        "healorhalt_llm_min_confidence",
        "Lowest model confidence a Tier 2 heal is accepted at",
        default=str(LLM_MIN_CONFIDENCE),
    )
    defaults = VerdictConfig()
    for name, help_text in VERDICT_INI.items():
        parser.addini(f"healorhalt_verdict_{name}", help_text, default=str(getattr(defaults, name)))


def pytest_configure(config: pytest.Config) -> None:
    heal = config.getoption("--heal")
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
    llm_client = None
    if heal in ("llm", "full"):
        from healorhalt.heal.tier2_llm import make_client

        llm_client = make_client(timeout=float(pytestconfig.getini("healorhalt_llm_timeout")))
    if heal != "off":
        healer = Healer(
            store,
            bundle_id,
            heal,
            run_id=pytestconfig.stash[RUN_ID],
            test=request.node.nodeid,
            mutation=mutation,
            threshold=float(pytestconfig.getini("healorhalt_heal_threshold")),
            llm_client=llm_client,
            llm_model=str(pytestconfig.getini("healorhalt_llm_model")),
            llm_min_confidence=float(pytestconfig.getini("healorhalt_llm_min_confidence")),
            verdict=VerdictConfig(
                **{
                    name: float(pytestconfig.getini(f"healorhalt_verdict_{name}"))
                    for name in VERDICT_INI
                }
            ),
        )
    recorder = Recorder(driver) if pytestconfig.getoption("--record") else None
    app = App(
        driver,
        bundle_id,
        healer=healer,
        recorder=recorder,
        screens=locators.screens(),
        evidence_dir=pytestconfig.rootpath / EVIDENCE_DIR,
    )
    app.launch(mutation=mutation)
    return app


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Judge a test's heals by its outcome, then save fingerprints if it passed, or a screenshot
    and page source if it failed."""
    report = yield
    funcargs = getattr(item, "funcargs", {})
    app, store = funcargs.get("app"), funcargs.get("store")
    if not isinstance(app, App):
        return report
    ended = report.when == "call" or (report.when == "setup" and report.failed)
    if not ended:
        return report
    halted = call.excinfo is not None and call.excinfo.errisinstance(HealHalted)
    if halted and call.excinfo is not None:
        report.longrepr = str(call.excinfo.value)  # the verdict, not a traceback
    judged = app.finish(report.passed, report.longreprtext if report.failed else "", halted)
    if judged:
        verdicts = []
        for pending in judged:
            assert pending.verdict is not None
            verdicts.append((pending.verdict.decision, pending.evidence.key, str(pending.card)))
            if pending.verdict.decision == ESCALATE and report.passed:
                _write_patch(item.config, pending)
        report.user_properties.append((VERDICTS, verdicts))
        lines = [f"{d} {key}: {card}" for d, key, card in verdicts]
        report.sections.append(("healorhalt evidence", "\n".join(lines)))
    if report.failed:
        name = item.nodeid.replace("/", "_").replace("::", "__")
        png, _ = capture(app.driver, item.config.rootpath / FAILURES_DIR, name)
        report.sections.append(("healorhalt", f"screenshot and page source saved next to {png}"))
    elif app.recorder is not None and isinstance(store, Store):
        app.recorder.flush(store, app.bundle_id, item.nodeid)
    return report


def _write_patch(config: pytest.Config, pending: PendingHeal) -> None:
    """Write the healed locator as a patch to the locator file, beside the evidence card."""
    healed = pending.heal.locator  # the same logical name, pointed at the healed element
    if healed.params or pending.card is None:
        return
    path = _ini_path(config, "healorhalt_locators")
    patch = locator_patch(
        path.read_text(encoding="utf-8"),
        str(path.relative_to(config.rootpath)),
        healed.name,
        healed.strategy,
        healed.value,
    )
    if patch:
        (pending.card.parent / "locators.patch").write_text(patch, encoding="utf-8")


def pytest_report_teststatus(
    report: pytest.TestReport, config: pytest.Config
) -> tuple[str, str, str] | None:
    """A passing test with a heal waiting for review shows as PENDING REVIEW, not PASSED."""
    if report.when != "call" or not report.passed:
        return None
    for name, verdicts in report.user_properties:
        if name == VERDICTS and any(d == ESCALATE for d, _, _ in cast(list[Any], verdicts)):
            return "pending review", "R", "PENDING REVIEW"
    return None


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
    llm_seconds: list[float] = []
    for heal in heals:
        score = f"{heal['score']:.2f}" if heal["score"] is not None else "-"
        new = f"{heal['new_strategy']}={heal['new_value']}" if heal["new_strategy"] else "-"
        verdict = f"{heal['verdict']:<8} " if heal["verdict"] else ""
        line = (
            f"#{heal['id']} {verdict}{heal['outcome']:<15} tier {heal['tier']} score {score}  "
            f"{heal['key']}: {heal['old_strategy']}={heal['old_value']} -> {new}"
        )
        if heal["latency_s"] is not None:
            llm_seconds.append(heal["latency_s"])
            line += f"  ({heal['latency_s']:.1f} s)"
        terminalreporter.write_line(line)
        if heal["verdict"]:
            terminalreporter.write_line(f"    {heal['reason']}")
            terminalreporter.write_line(f"    evidence: {heal['evidence']}")
    if llm_seconds:
        average, slowest = sum(llm_seconds) / len(llm_seconds), max(llm_seconds)
        terminalreporter.write_line(
            f"{len(llm_seconds)} LLM calls, {average:.1f} s on average, {slowest:.1f} s max"
        )
