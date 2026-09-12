"""Read-only schema, aggregate backlog and actual worker registration; never call the VLM."""

import argparse
import asyncio
import json
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from daengs_backend.config import settings
from daengs_backend.tasks.territory import app

LEASE_COLUMNS = {
    "vision_lease_token",
    "vision_lease_until",
    "vision_attempts",
    "vision_available_at",
    "vision_dispatch_after",
    "vision_retry_reason",
}


async def database_inventory():
    engine = create_async_engine(
        settings.database_url, poolclass=NullPool, connect_args={"timeout": 5}
    )
    try:
        async with engine.connect() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            await db.execute(text("SET LOCAL statement_timeout = '5000'"))
            columns = set(
                await db.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name = 'territory_attempts'"
                    )
                )
            )
            counts = await db.execute(
                text("SELECT status, count(*) AS count FROM territory_attempts GROUP BY status")
            )
            return {
                "lease_columns_present": LEASE_COLUMNS <= columns,
                "attempt_counts": [dict(row) for row in counts.mappings()],
            }
    finally:
        await engine.dispose()


def inventory():
    result = asyncio.run(database_inventory())
    destination = "celery@" + os.environ["HOSTNAME"]
    inspector = app.control.inspect(destination=[destination], timeout=10)
    registered = (inspector.registered() or {}).get(destination, [])
    queues = (inspector.active_queues() or {}).get(destination, [])
    result["worker_tasks"] = sorted(name for name in registered if name.startswith("territory."))
    result["worker_queue_ready"] = any(queue["name"] == "territory-vision" for queue in queues)
    result["ready"] = (
        result["lease_columns_present"]
        and {"territory.verify_photo", "territory.recover_photos"} <= set(registered)
        and result["worker_queue_ready"]
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    try:
        result = inventory()
    except Exception as exc:  # noqa: BLE001 - never log connection values or provider secrets
        print(json.dumps({"inventory_error": type(exc).__name__}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=True))
    if args.require_ready and not result["ready"]:
        raise SystemExit(1)
