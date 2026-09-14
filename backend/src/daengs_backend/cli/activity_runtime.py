"""Read-only activity preflight and container heartbeat checks. No start-season path."""

import argparse
import asyncio
import json
import time
from pathlib import Path

VERIFIERS = (
    "2026-09-03_territory_visits",
    "2026-09-05_territory_claims",
    "2026-09-06_activity_game",
    "2026-09-08_certified_territory",
    "2026-09-10_activity_rewards",
    "2026-09-10_territory_expiry",
    "2026-09-10_activity_monthly",
    "2026-09-10_territory_bookmarks",
)


async def check_schema(directory):
    import asyncpg

    from daengs_backend.config import settings

    conn = await asyncpg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password.get_secret_value(),
        database=settings.db_name,
        timeout=10,
        command_timeout=15,
    )
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            await conn.execute("SET LOCAL search_path=public")
            for name in VERIFIERS:
                await conn.execute((directory / f"verify_{name}.sql").read_text("utf-8"))
            seasons = await conn.fetchval(
                "SELECT count(*) FROM activity_seasons WHERE status='ACTIVE'"
            )
            pending = await conn.fetchval(
                "SELECT count(*) FROM activity_accounts WHERE revision > processed_revision"
            )
        return {
            "game_enabled": settings.activity_game_enabled,
            "schema_checks": len(VERIFIERS),
            "active_seasons": seasons,
            "pending_accounts": pending,
        }
    finally:
        await conn.close()


def beat_healthy(path, now):
    try:
        value = json.loads(path.read_text())
        return value["state"] in {"disabled", "leader"} and 0 <= now - value["at"] < 30
    except (OSError, ValueError, KeyError, TypeError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--beat-health", action="store_true")
    parser.add_argument("--schema-dir", type=Path, default=Path("/schema-checks"))
    args = parser.parse_args()
    if args.beat_health:
        from daengs_backend.tasks.activity_scheduler import HEARTBEAT

        raise SystemExit(0 if beat_healthy(HEARTBEAT, time.time()) else 1)
    try:
        print(json.dumps(asyncio.run(check_schema(args.schema_dir))))
    except Exception as exc:  # noqa: BLE001 -- CLI boundary must not print connection secrets
        # DB/broker error strings can contain connection information.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
