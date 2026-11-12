"""Phase 3 exit check: the LLM tier recovers cases the heuristics missed, in under 10 s a heal.

For each case in bench/mutations/demo.yaml, its affected tests run under the mutation three
times: with --heal=off, which must fail (or the case is invalid); with --heal=heuristic; and with
--heal=llm, which adds Tier 2 behind Tier 1. Latency is averaged over every Tier 2 call made.
Needs Appium, Ollama, a built app (make app) and recorded fingerprints (make record).
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
RESULTS = Path("artifacts/phase3/results.json")
MAX_AVERAGE_LATENCY = 10.0
HEAL_FIELDS = ("key", "outcome", "tier", "score", "new_strategy", "new_value", "latency_s")


def run(tests: list[str], mutation: str, heal: str) -> bool:
    command = ["uv", "run", "pytest", *tests, f"--mutation={mutation}", f"--heal={heal}", "-q"]
    return subprocess.run(command, capture_output=True, text=True).returncode == 0


def heals_since(store: Store, heal_id: int) -> list[dict[str, object]]:
    rows = store.db.execute(
        "SELECT heals.*, llm_calls.latency_s FROM heals"
        " LEFT JOIN llm_calls ON llm_calls.heal_id = heals.id"
        " WHERE heals.id > ? ORDER BY heals.id",
        (heal_id,),
    )
    return [{k: row[k] for k in HEAL_FIELDS} for row in rows]


def last_heal_id(store: Store) -> int:
    return int(store.db.execute("SELECT COALESCE(MAX(id), 0) FROM heals").fetchone()[0])


def main() -> int:
    cases = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["cases"]
    store = Store(DB)
    if store.fingerprint_count("com.healorhalt.pantry") == 0:
        sys.exit("no fingerprints recorded; run `make record` first")
    results: list[dict[str, Any]] = []
    for case in cases:
        tests = case["tests_affected"]
        result: dict[str, Any] = {"id": case["id"], "kind": case["kind"]}
        result["broke_without_healing"] = broke = not run(tests, case["id"], "off")
        for mode in ("heuristic", "llm"):
            before = last_heal_id(store)
            passed = run(tests, case["id"], mode)
            result[mode] = {"recovered": broke and passed, "heals": heals_since(store, before)}
        results.append(result)
        marks = {m: "yes" if result[m]["recovered"] else "no " for m in ("heuristic", "llm")}
        print(
            f"{case['id']} {case['kind']:<9} broke={'yes' if broke else 'NO '} "
            f"heuristic={marks['heuristic']} llm={marks['llm']}",
            flush=True,
        )
        for heal in result["llm"]["heals"]:
            score = f"{heal['score']:.2f}" if heal["score"] is not None else "-"
            latency = f" {heal['latency_s']:.1f} s" if heal["latency_s"] is not None else ""
            print(
                f"    tier {heal['tier']} {heal['outcome']:<15} {score}  {heal['key']}"
                f" -> {heal['new_strategy']}={heal['new_value']}{latency}"
            )

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=2), encoding="utf-8")

    def recovered(r: dict[str, Any], mode: str) -> bool:
        return bool(r[mode]["recovered"])

    invalid = [r["id"] for r in results if not r["broke_without_healing"]]
    missed = [r for r in results if r["broke_without_healing"] and not recovered(r, "heuristic")]
    rescued = [r["id"] for r in missed if recovered(r, "llm")]
    lost = [r["id"] for r in results if recovered(r, "heuristic") and not recovered(r, "llm")]
    latencies = [
        float(h["latency_s"])
        for r in results
        for h in r["llm"]["heals"]
        if h["latency_s"] is not None
    ]
    average = sum(latencies) / len(latencies) if latencies else 0.0
    print(f"\nheuristic recovered {len(results) - len(missed) - len(invalid)} of {len(results)}")
    print(f"llm recovered {sum(recovered(r, 'llm') for r in results)} of {len(results)}")
    print(f"heuristics missed {[r['id'] for r in missed]}; llm recovered {rescued}")
    if lost:
        print(f"recovered by heuristic but not llm: {lost}")
    if latencies:
        print(f"{len(latencies)} LLM calls: {average:.1f} s on average, {max(latencies):.1f} s max")
    if invalid:
        print(f"invalid cases (nothing broke with --heal=off): {', '.join(map(str, invalid))}")
    print(f"details: {RESULTS}")
    ok = bool(rescued) and not lost and not invalid and average < MAX_AVERAGE_LATENCY
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
