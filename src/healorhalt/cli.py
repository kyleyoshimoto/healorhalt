"""Command-line tools for reviewing heals: ``healorhalt heals`` and ``healorhalt approve``."""

import argparse
from pathlib import Path

from healorhalt.store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="healorhalt")
    parser.add_argument("--db", type=Path, default=Path(".healorhalt/healorhalt.db"))
    commands = parser.add_subparsers(dest="command", required=True)
    heals = commands.add_parser("heals", help="List recent heal attempts")
    heals.add_argument("--limit", type=int, default=20)
    approve = commands.add_parser("approve", help="Add a heal's locator to the Tier 0 cache")
    approve.add_argument("heal_id", type=int)
    args = parser.parse_args(argv)

    store = Store(args.db)
    try:
        if args.command == "heals":
            for heal in store.heals(limit=args.limit):
                score = f"{heal['score']:.2f}" if heal["score"] is not None else "-"
                print(
                    f"#{heal['id']} {heal['created_at']} {heal['outcome']:<15} score {score} "
                    f"{heal['key']} -> {heal['new_strategy'] or '-'}={heal['new_value'] or '-'}"
                    f"  [{heal['mutation'] or 'no mutation'}] {heal['test']}"
                )
        else:
            try:
                row = store.approve(args.heal_id)
            except LookupError as e:
                parser.error(str(e))
            print(
                f"approved #{row['id']}: {row['key']} -> {row['new_strategy']}={row['new_value']}"
            )
    finally:
        store.close()
    return 0
