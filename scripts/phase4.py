"""Phase 4 exit check: none of the bug mutations ends in HEAL under the verdict engine.

For each case in bench/mutations/demo.yaml, its affected tests run under the mutation three times:
with --heal=off, which must fail (or the case is invalid); with --heal=llm, the naive mode that
applies whatever the tiers propose; and with --heal=full, which adds the verdict engine.

A case's outcome under full mode is HALT if any affected test failed, ESCALATE if they all passed
but a heal waits for review, and HEAL if they passed with every heal trusted. A failed test counts
as HALT whether the verdict engine stopped it or the test's own assertion did; the results say
which. Needs Appium, Ollama, a built app (make app) and a fresh recording (make record).
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from healorhalt.store import Store

CATALOG = Path("bench/mutations/demo.yaml")
DB = Path(".healorhalt/healorhalt.db")
RESULTS = Path("artifacts/phase4/results.json")
HEAL_FIELDS = ("key", "tier", "outcome", "score", "verdict", "reason", "evidence", "new_value")


def run(tests: list[str], mutation: str, heal: str) -> bool:
    command = ["uv", "run", "pytest", *tests, f"--mutation={mutation}", f"--heal={heal}", "-q"]
    return subprocess.run(command, capture_output=True, text=True).returncode == 0


def last_heal_id(store: Store) -> int:
    return int(store.db.execute("SELECT COALESCE(MAX(id), 0) FROM heals").fetchone()[0])


def heals_since(store: Store, heal_id: int) -> list[dict[str, Any]]:
    rows = store.db.execute("SELECT * FROM heals WHERE id > ? ORDER BY id", (heal_id,))
    return [{k: row[k] for k in HEAL_FIELDS} for row in rows]


def outcome(passed: bool, heals: list[dict[str, Any]]) -> str:
    verdicts = {h["verdict"] for h in heals}
    if not passed:
        return "HALT"
    if "ESCALATE" in verdicts:
        return "ESCALATE"
    return "HEAL" if heals else "PASS"


def main() -> int:
    cases = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["cases"]
    store = Store(DB)
    if store.fingerprint_count("com.healorhalt.pantry") == 0:
        sys.exit("no fingerprints recorded; run `make record` first")
    if store.db.execute("SELECT COUNT(*) FROM screens").fetchone()[0] == 0:
        sys.exit("no screens recorded; run `make record` again for Phase 4")
    results: list[dict[str, Any]] = []
    for case in cases:
        tests = case["tests_affected"]
        bug = case["expected"] != "HEAL"
        result: dict[str, Any] = {"id": case["id"], "kind": case["kind"], "bug": bug}
        result["broke_without_healing"] = not run(tests, case["id"], "off")
        result["naive_passed"] = run(tests, case["id"], "llm")
        before = last_heal_id(store)
        passed = run(tests, case["id"], "full")
        heals = heals_since(store, before)
        result |= {"full_passed": passed, "full": outcome(passed, heals), "heals": heals}
        result["halted_by_engine"] = any(h["verdict"] == "HALT" for h in heals)
        results.append(result)
        naive = "green" if result["naive_passed"] else "red  "
        how = ""
        if result["full"] == "HALT":
            how = " (verdict engine)" if result["halted_by_engine"] else " (test assertion)"
        print(
            f"{case['id']} {case['kind']:<9} {'bug   ' if bug else 'benign'} "
            f"broke={'yes' if result['broke_without_healing'] else 'NO '} naive={naive} "
            f"full={result['full']}{how}",
            flush=True,
        )
        for heal in heals:
            score = f"{heal['score']:.2f}" if heal["score"] is not None else "-"
            print(
                f"    tier {heal['tier']} {heal['verdict'] or '-':<8} {score}  {heal['key']}"
                f" -> {heal['new_value'] or '-'}: {heal['reason'] or heal['outcome']}"
            )

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=2), encoding="utf-8")

    invalid = [r["id"] for r in results if not r["broke_without_healing"]]
    bugs = [r for r in results if r["bug"]]
    benign = [r for r in results if not r["bug"]]
    false_heals = [r["id"] for r in bugs if r["full"] in ("HEAL", "PASS")]
    naive_false = [r["id"] for r in bugs if r["naive_passed"]]

    def count(rows: list[dict[str, Any]], verdict: str) -> int:
        return sum(r["full"] == verdict for r in rows)

    print(
        f"\nbugs ({len(bugs)}): HALT {count(bugs, 'HALT')} "
        f"(verdict engine {sum(r['halted_by_engine'] for r in bugs if r['full'] == 'HALT')}), "
        f"ESCALATE {count(bugs, 'ESCALATE')}, HEAL {len(false_heals)}"
    )
    print(f"naive LLM mode turned {len(naive_false)} bugs green: {naive_false}")
    print(
        f"benign ({len(benign)}): HEAL {count(benign, 'HEAL')}, "
        f"ESCALATE {count(benign, 'ESCALATE')}, HALT {count(benign, 'HALT')}"
    )
    if false_heals:
        print(f"false heals: {', '.join(false_heals)}")
    if invalid:
        print(f"invalid cases (nothing broke with --heal=off): {', '.join(invalid)}")
    print(f"details: {RESULTS}")
    return 0 if not false_heals and not invalid else 1


if __name__ == "__main__":
    sys.exit(main())
