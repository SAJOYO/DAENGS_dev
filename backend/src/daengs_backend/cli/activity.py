"""Explicit administration: python -m daengs_backend.cli.activity --help."""

import argparse
import asyncio
import json
from dataclasses import fields
from pathlib import Path

from daengs_backend.services.activity_core.first_season_policy import Rules as FirstSeasonRules
from daengs_backend.services.activity_core.first_season_rewards import REWARD_VERSION
from daengs_backend.services.activity_core.game_policy import Rules


def parse_rules(path):
    values = json.loads(Path(path).read_text(encoding="utf-8"))
    rule_class = (
        FirstSeasonRules
        if isinstance(values, dict) and values.get("version") == REWARD_VERSION
        else Rules
    )
    if not isinstance(values, dict) or set(values) != {f.name for f in fields(rule_class)}:
        raise ValueError("rules JSON must explicitly specify every Rules field")
    return rule_class(**values)


async def run(args):
    from daengs_backend.core.database import worker_session
    from daengs_backend.services import activity, activity_game

    async with worker_session() as db:
        if args.command in {"start-season", "start-monthly"}:
            activity.enabled()
            if args.command == "start-monthly":
                from daengs_backend.services.activity_core.monthly_calendar import month

                args.starts_ms = activity_game.now_ms()
                args.season_id, _, args.ends_ms = month(args.starts_ms)
            season = await activity_game.create_season(
                db,
                args.season_id,
                args.starts_ms,
                args.ends_ms,
                parse_rules(args.rules),
                monthly=args.command == "start-monthly",
            )
            print(
                json.dumps({"season_id": season.id, "coverage_start_ms": season.coverage_start_ms})
            )
        elif args.command == "process":
            print(json.dumps({"processed": await activity.process_pending(db, limit=args.limit)}))
        elif args.command == "rebuild":
            await activity.rebuild(db)
            print(json.dumps({"status": "queued"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start-season")
    start.add_argument("season_id")
    start.add_argument("--starts-ms", type=int, required=True)
    start.add_argument("--ends-ms", type=int, required=True)
    start.add_argument("--rules", required=True, help="JSON path; no implicit product balance")
    monthly = commands.add_parser(
        "start-monthly", help="Explicit first activation; KST month end and automatic successors"
    )
    monthly.add_argument("--rules", required=True, help="Complete first-season reward rules JSON")
    process = commands.add_parser("process")
    process.add_argument("--limit", type=int, default=100)
    commands.add_parser("rebuild")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
