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

# Persisted budget in territory_attempts_vision_attempts_check, paired with the worker in tests.
# This script is copied alone into the live worker, which may still run an older code revision.
MAX_ATTEMPTS = 2
PHOTO_CLEANUP_BLOCKED_REASON = "photo_cleanup_conflict"


async def backlog_snapshot(connection, *, max_attempts=MAX_ATTEMPTS):
    """Standalone, read-only aggregate; requires no newly deployed ORM/service helpers."""
    result = await connection.execute(
        text("""
            WITH candidates AS (
                SELECT status = 'VISION_PENDING' AS pending,
                       status IN ('VERIFIED', 'REJECTED', 'FAILED')
                           AND photo_redacted_at IS NULL AS cleanup,
                       vision_lease_until, vision_attempts, created_at,
                       vision_retry_reason = :cleanup_blocked_reason AS cleanup_blocked,
                       vision_available_at <= CURRENT_TIMESTAMP
                           AND vision_dispatch_after <= CURRENT_TIMESTAMP
                           AND (vision_lease_until IS NULL OR vision_lease_until <= CURRENT_TIMESTAMP)
                           AND (status = 'VISION_PENDING'
                               OR vision_retry_reason IS DISTINCT FROM :cleanup_blocked_reason)
                           AS due
                FROM territory_attempts
                WHERE status = 'VISION_PENDING'
                   OR (status IN ('VERIFIED', 'REJECTED', 'FAILED') AND photo_redacted_at IS NULL)
            )
            SELECT CURRENT_TIMESTAMP AS checked_at,
                   count(*) FILTER (WHERE pending) AS pending_count,
                   count(*) FILTER (WHERE pending AND vision_lease_until > CURRENT_TIMESTAMP)
                       AS active_lease_count,
                   count(*) FILTER (WHERE pending AND vision_lease_until <= CURRENT_TIMESTAMP)
                       AS expired_lease_count,
                   count(*) FILTER (WHERE pending AND vision_attempts >= :max_attempts
                       AND (vision_lease_until IS NULL OR vision_lease_until <= CURRENT_TIMESTAMP))
                       AS exhausted_awaiting_completion_count,
                   count(*) FILTER (WHERE pending AND due) AS pending_dispatch_due_count,
                   count(*) FILTER (WHERE cleanup) AS cleanup_pending_count,
                   count(*) FILTER (WHERE cleanup AND cleanup_blocked) AS cleanup_blocked_count,
                   count(*) FILTER (WHERE cleanup AND due) AS cleanup_dispatch_due_count,
                   count(*) FILTER (WHERE due) AS dispatch_due_count,
                   CASE WHEN min(created_at) FILTER (WHERE pending) IS NOT NULL
                        THEN greatest(0, extract(epoch FROM CURRENT_TIMESTAMP
                             - min(created_at) FILTER (WHERE pending)))
                        ELSE NULL END AS oldest_pending_created_age_seconds
            FROM candidates
        """),
        {"max_attempts": max_attempts, "cleanup_blocked_reason": PHOTO_CLEANUP_BLOCKED_REASON},
    )
    snapshot = dict(result.mappings().one())
    snapshot["checked_at"] = snapshot["checked_at"].isoformat()
    age = snapshot["oldest_pending_created_age_seconds"]
    snapshot["oldest_pending_created_age_seconds"] = None if age is None else float(age)
    return snapshot


async def database_inventory(*, engine=None):
    owns_engine = engine is None
    engine = engine or create_async_engine(
        settings.database_url, poolclass=NullPool, connect_args={"timeout": 5}
    )
    try:
        async with engine.connect() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
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
            backlog = await backlog_snapshot(db) if LEASE_COLUMNS <= columns else None
            return {
                "lease_columns_present": LEASE_COLUMNS <= columns,
                "attempt_counts": [dict(row) for row in counts.mappings()],
                "backlog": backlog,
            }
    finally:
        if owns_engine:
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
