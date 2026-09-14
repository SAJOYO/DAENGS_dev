"""Preview or explicitly schedule missing public contexts. No LLM calls or board writes."""

import argparse
import asyncio
import json
from datetime import datetime
from uuid import UUID

from daengs_backend.core.database import SessionLocal, engine
from daengs_backend.services.walk_records.backfill import run


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--walk-id", type=UUID, action="append")
    value.add_argument("--since", type=datetime.fromisoformat)
    value.add_argument("--until", type=datetime.fromisoformat)
    value.add_argument("--after", type=UUID)
    value.add_argument("--limit", type=int, default=1)
    value.add_argument("--apply", action="store_true")
    value.add_argument("--expected-plan")
    return value


async def main(args):
    try:
        result = await run(
            SessionLocal,
            walk_ids=args.walk_id,
            since=args.since,
            until=args.until,
            after=args.after,
            limit=args.limit,
            apply=args.apply,
            expected_plan=args.expected_plan,
        )
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - no SQL, coordinates, notes, tokens or provider errors
        print(
            json.dumps(
                {
                    "format": "walk-context-backfill-v1",
                    "ok": False,
                    "error_type": type(exc).__name__,
                }
            )
        )
        return 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parser().parse_args())))
