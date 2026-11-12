"""Ask Tier 2 directly about every mutation case, using the saved fixtures.

Unlike the live run, this skips Tier 1, so it measures the LLM on every case, easy ones
included. It needs Ollama but no simulator: page sources come from tests/unit/fixtures/xml and
screenshots from artifacts/fixtures/png (both written by scripts/capture_fixtures.py). With
--record, each answer is saved to tests/unit/fixtures/llm/ for the contract tests.
"""

import argparse
import json
import sys
from pathlib import Path

from healorhalt.fingerprint import fingerprint, parse
from healorhalt.heal.tier2_llm import DEFAULT_TIMEOUT, chosen, make_client, propose, warm_up
from healorhalt.locator import LocatorMap

XML = Path("tests/unit/fixtures/xml")
PNG = Path("artifacts/fixtures/png")
RECORDED = Path("tests/unit/fixtures/llm")
LOCATORS = LocatorMap.from_yaml(Path("tests/demo/locators.yaml"))

# (case, screen, locator, action, element before the mutation, the same element after).
# A screen "a>b" takes the fingerprint from screen a and asks about screen b, where the element
# isn't; the right answer there is element_still_exists false (expected None).
CASES: list[tuple[str, str, str, str, str, str | None]] = [
    ("M01", "login", "login.submit", "tap", "login.submit", "login.signIn"),
    ("M02", "add", "add.save", "tap", "add.save", "add.save"),
    ("M03", "list", "list.add", "tap", "list.add", "list.add"),
    ("M04", "detail", "detail.delete", "tap", "detail.delete", "detail.delete"),
    ("M05", "detail", "detail.edit", "tap", "detail.edit", "detail.modify"),
    ("M06", "settings", "settings.logout", "tap", "settings.logout", "settings.logout"),
    ("M07", "list", "list.search", "type text into", "list.search", "list.search"),
    ("M08", "login", "login.username", "type text into", "login.username", "login.username"),
    ("M09", "settings", "settings.showQuantities", "toggle", "settings.showQuantities",
     "settings.quantities"),
    ("M10", "list", "list.settings", "tap", "list.settings", "list.preferences"),
    ("M11", "login", "login.submit", "tap", "login.submit", "Continue"),
    ("M12", "settings", "settings.logout", "tap", "settings.logout", "settings.account.signOut"),
    ("M13", "detail", "detail.edit", "tap", "detail.edit", "Change"),
    ("M14", "list", "list.settings", "tap", "list.settings", "nav.preferences"),
    ("N01", "detail>list", "detail.delete", "tap", "detail.delete", None),
    ("N02", "settings>detail", "settings.logout", "tap", "settings.logout", None),
    ("N03", "add>list", "add.save", "tap", "add.save", None),
]  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", action="store_true", help="save answers as test fixtures")
    parser.add_argument("--no-images", action="store_true", help="send the text prompt only")
    args = parser.parse_args()
    client = make_client(timeout=DEFAULT_TIMEOUT)
    warm_up(client)
    correct, latencies = 0, []
    for case, screen, name, action, before, after in CASES:
        fp_screen, _, now_screen = screen.partition(">")
        base = parse((XML / f"base_{fp_screen}.xml").read_text(encoding="utf-8"))
        (node,) = base.xpath(
            "//*[@name=$n][@accessible='true' or @type!='XCUIElementTypeOther']", n=before
        )
        fp = fingerprint(name, node, base)
        shot = f"base_{now_screen}" if now_screen else f"{case}_{screen}"
        root = parse((XML / f"{shot}.xml").read_text(encoding="utf-8"))
        png_path = PNG / f"{shot}.png"
        png = None if args.no_images or not png_path.exists() else png_path.read_bytes()
        locator = LOCATORS.get(name)
        screen_, call = propose(fp, root, png, client, name, locator.intent, action)
        mark = chosen(screen_, call.proposal) if call.proposal else None
        if after is None:
            ok = call.proposal is not None and mark is None
        else:
            ok = mark is not None and after in (
                [mark.node.get("name")] + [a.get("name") for a in mark.node.iterancestors()][:2]
            )
        correct += ok
        latencies.append(call.latency_s)
        conf = f"{call.proposal.confidence:.2f}" if call.proposal else "-"
        picked = mark.node.get("name") if mark else call.error or "none"
        print(
            f"{case} {'ok ' if ok else 'BAD'} {call.latency_s:5.1f} s  conf {conf}  "
            f"picked {picked!r}  ({call.prompt_tokens} in / {call.completion_tokens} out)",
            flush=True,
        )
        if call.error and call.response:
            print(f"    response: {call.response[:300]!r}")
        if args.record and call.response is not None:
            RECORDED.mkdir(parents=True, exist_ok=True)
            (RECORDED / f"{case}.json").write_text(
                json.dumps(
                    {
                        "case": case,
                        "fingerprint_from": f"base_{fp_screen}",
                        "page_source": shot,
                        "locator": name,
                        "action": action,
                        "before": before,
                        "expected": after,
                        "model": call.model,
                        "response": call.response,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
    average = sum(latencies) / len(latencies)
    print(
        f"\n{correct} of {len(CASES)} correct, "
        f"{average:.1f} s on average, {max(latencies):.1f} s max"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
