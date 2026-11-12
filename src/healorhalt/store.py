"""SQLite access for fingerprints, heal cache, audit log and results."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from healorhalt.fingerprint import Fingerprint

SCHEMA = """
CREATE TABLE IF NOT EXISTS fingerprints (
    app TEXT NOT NULL,
    key TEXT NOT NULL,
    data TEXT NOT NULL,
    screenshot BLOB,
    test TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (app, key)
);
CREATE TABLE IF NOT EXISTS heals (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    run_id TEXT NOT NULL,
    app TEXT NOT NULL,
    test TEXT NOT NULL,
    mutation TEXT,
    key TEXT NOT NULL,
    tier INTEGER,
    outcome TEXT NOT NULL,
    old_strategy TEXT NOT NULL,
    old_value TEXT NOT NULL,
    new_strategy TEXT,
    new_value TEXT,
    score REAL,
    candidates TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS heal_cache (
    app TEXT NOT NULL,
    key TEXT NOT NULL,
    strategy TEXT NOT NULL,
    value TEXT NOT NULL,
    heal_id INTEGER NOT NULL REFERENCES heals (id),
    approved_at TEXT NOT NULL,
    PRIMARY KEY (app, key)
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY,
    heal_id INTEGER NOT NULL REFERENCES heals (id),
    model TEXT NOT NULL,
    latency_s REAL NOT NULL,
    prompt_tokens_estimate INTEGER NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    error TEXT,
    prompt TEXT NOT NULL,
    response TEXT
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class HealRecord:
    """One heal attempt, successful or not, as written to the audit log."""

    run_id: str
    app: str
    test: str
    mutation: str | None
    key: str
    tier: int | None
    outcome: str
    """healed, not_found, no_fingerprint, below_threshold (Tier 1), or for Tier 2 no_proposal
    (the model answered that the element is gone, or named no drawn box), low_confidence or
    llm_error (timeout, unreachable server, malformed answer)."""
    old_strategy: str
    old_value: str
    new_strategy: str | None = None
    new_value: str | None = None
    score: float | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def save_fingerprint(self, app: str, fp: Fingerprint, screenshot: bytes, test: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO fingerprints VALUES (?, ?, ?, ?, ?, ?)",
                (app, fp.key, fp.to_json(), screenshot, test, now()),
            )

    def fingerprint(self, app: str, key: str) -> Fingerprint | None:
        row = self.db.execute(
            "SELECT data FROM fingerprints WHERE app = ? AND key = ?", (app, key)
        ).fetchone()
        return Fingerprint.from_json(row["data"]) if row else None

    def fingerprint_count(self, app: str) -> int:
        row = self.db.execute("SELECT COUNT(*) FROM fingerprints WHERE app = ?", (app,)).fetchone()
        return int(row[0])

    def log_heal(self, record: HealRecord) -> int:
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO heals (created_at, run_id, app, test, mutation, key, tier, outcome,"
                " old_strategy, old_value, new_strategy, new_value, score, candidates)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now(),
                    record.run_id,
                    record.app,
                    record.test,
                    record.mutation,
                    record.key,
                    record.tier,
                    record.outcome,
                    record.old_strategy,
                    record.old_value,
                    record.new_strategy,
                    record.new_value,
                    record.score,
                    json.dumps(record.candidates),
                ),
            )
        return int(cursor.lastrowid or 0)

    def log_llm_call(
        self,
        heal_id: int,
        model: str,
        latency_s: float,
        prompt: str,
        prompt_tokens_estimate: int,
        response: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        error: str | None = None,
    ) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO llm_calls (heal_id, model, latency_s, prompt_tokens_estimate,"
                " prompt_tokens, completion_tokens, error, prompt, response)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    heal_id,
                    model,
                    latency_s,
                    prompt_tokens_estimate,
                    prompt_tokens,
                    completion_tokens,
                    error,
                    prompt,
                    response,
                ),
            )

    def llm_call(self, heal_id: int) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.db.execute(
            "SELECT * FROM llm_calls WHERE heal_id = ?", (heal_id,)
        ).fetchone()
        return row

    def heals(self, run_id: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
        """A run's heal attempts in order, or the latest `limit` attempts across runs.

        Each row also has ``latency_s``, the LLM call's time for Tier 2 attempts, else None.
        """
        if run_id is not None:
            return list(
                self.db.execute(
                    "SELECT heals.*, llm_calls.latency_s FROM heals"
                    " LEFT JOIN llm_calls ON llm_calls.heal_id = heals.id"
                    " WHERE run_id = ? ORDER BY heals.id",
                    (run_id,),
                )
            )
        return list(
            self.db.execute(
                "SELECT heals.*, llm_calls.latency_s FROM heals"
                " LEFT JOIN llm_calls ON llm_calls.heal_id = heals.id"
                " ORDER BY heals.id DESC LIMIT ?",
                (limit,),
            )
        )

    def approve(self, heal_id: int) -> sqlite3.Row:
        """Copy a successful heal's locator into the Tier 0 cache."""
        row: sqlite3.Row | None = self.db.execute(
            "SELECT * FROM heals WHERE id = ?", (heal_id,)
        ).fetchone()
        if row is None or row["outcome"] != "healed":
            raise LookupError(f"heal {heal_id} doesn't exist or didn't heal")
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO heal_cache VALUES (?, ?, ?, ?, ?, ?)",
                (row["app"], row["key"], row["new_strategy"], row["new_value"], heal_id, now()),
            )
        return row

    def cached(self, app: str, key: str) -> tuple[str, str] | None:
        row = self.db.execute(
            "SELECT strategy, value FROM heal_cache WHERE app = ? AND key = ?", (app, key)
        ).fetchone()
        return (row["strategy"], row["value"]) if row else None
