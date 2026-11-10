"""Phase 2 exit check: at least 8 of the 10 benign mutations recover with --heal=heuristic.

For each case in bench/mutations/demo.yaml, its affected tests run twice under the mutation:
with --heal=off, which must fail (or the case doesn't break anything and is invalid), then with
--heal=heuristic, which recovers the case if every affected test passes. Needs Appium running,
a built app (make app) and recorded fingerprints (make record).
"""

import json
import subprocess
import sys
from pathlib import Path

import yaml

from healorhalt.store import Store

CATALOG = Path("bench/mutations/demo.yaml")
DB = Path(".healorhalt/healorhalt.db")
RESULTS = Path("artifacts/phase2/results.json")
REQUIRED = 8
HEAL_FIELDS = ("key", "outcome", "tier", "score", "new_strategy", "new_value")


def run(tests: list[str], mutation: str, heal: str) -> bool:
    command = ["uv", "run", "pytest", *tests, f"--mutation={mutation}", f"--heal={heal}", "-q"]
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode == 0


def last_heal_id(store: Store) -> int:
    row = store.db.execute("SELECT COALESCE(MAX(id), 0) FROM heals").fetchone()
    return int(row[0])


def main() -> int:
    cases = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["cases"]
    store = Store(DB)
    if store.fingerprint_count("com.healorhalt.pantry") == 0:
        sys.exit("no fingerprints recorded; run `make record` first")
    results = []
    for case in cases:
        tests = case["tests_affected"]
        broke = not run(tests, case["id"], "off")
        before = last_heal_id(store)
        healed = run(tests, case["id"], "heuristic")
        heals = [
            dict(row)
            for row in store.db.execute("SELECT * FROM heals WHERE id > ? ORDER BY id", (before,))
        ]
        results.append(
            {
                "id": case["id"],
                "kind": case["kind"],
                "expected": case["expected"],
                "broke_without_healing": broke,
                "recovered": broke and healed,
                "heals": [
                    {
                        k: h[k]
                        for k in ("key", "outcome", "tier", "score", "new_strategy", "new_value")
                    }
                    for h in heals
                ],
            }
        )
        r = results[-1]
        print(
            f"{case['id']} {case['kind']:<6} broke={'yes' if broke else 'NO'} "
            f"recovered={'yes' if r['recovered'] else 'no'}",
            flush=True,
        )
        for h in r["heals"]:
            score = f"{h['score']:.2f}" if h["score"] is not None else "-"
            print(
                f"    {h['outcome']:<15} {score}  {h['key']}"
                f" -> {h['new_strategy']}={h['new_value']}"
            )

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=2), encoding="utf-8")
    invalid = [r["id"] for r in results if not r["broke_without_healing"]]
    recovered = sum(r["recovered"] for r in results)
    print(f"\nrecovered {recovered} of {len(results)} benign mutations (need {REQUIRED})")
    if invalid:
        print(f"invalid cases (nothing broke with --heal=off): {', '.join(invalid)}")
    print(f"details: {RESULTS}")
    return 0 if recovered >= REQUIRED and not invalid else 1


if __name__ == "__main__":
    sys.exit(main())
