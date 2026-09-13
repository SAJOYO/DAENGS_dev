"""Read-only production aggregate and probe against real disposable PostgreSQL."""

import importlib.util
import json
import sys
import uuid
from datetime import timedelta

from sqlalchemy import event, func, select, text

from daengs_backend.models.territory import PHOTO_CLEANUP_BLOCKED_REASON, TerritoryAttempt
from daengs_backend.repositories import territory_vision as repo
from daengs_backend.services import territory_vision_jobs as jobs
from tests.territory.support.paths import REPO


def attempt(owner, now, **changes):
    values = {
        "id": uuid.uuid4(),
        "app_user_id": owner,
        "client_capture_id": uuid.uuid4(),
        "client_session_id": uuid.uuid4(),
        "site_id": "private-site",
        "captured_at": now,
        "capture_lat": 37.5,
        "capture_lng": 127,
        "site_lat": 37.5,
        "site_lng": 127,
        "accuracy_m": 1,
        "distance_m": 0,
        "is_mock": False,
        "status": "VISION_PENDING",
        "photo_storage_key": str(uuid.uuid4()),
        "photo_content_type": "image/jpeg",
        "photo_object_generation": "private-generation",
        "photo_size_bytes": 4,
        "vision_available_at": now,
        "vision_dispatch_after": now,
        "created_at": now - timedelta(minutes=10),
        "updated_at": now,
    }
    values.update(changes)
    if values["status"] in repo.TERMINAL:
        values.update(vision_model="test", vision_model_version="v1")
    return TerritoryAttempt(**values)


def probe_module():
    spec = importlib.util.spec_from_file_location(
        "vision_inventory_probe", REPO / "tools/inspect_territory_vision.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_empty_backlog_is_zero_with_unknown_oldest_age(database):
    assert probe_module().MAX_ATTEMPTS == jobs.MAX_ATTEMPTS
    assert probe_module().PHOTO_CLEANUP_BLOCKED_REASON == PHOTO_CLEANUP_BLOCKED_REASON
    async with database() as db:
        snapshot = await probe_module().backlog_snapshot(db, max_attempts=jobs.MAX_ATTEMPTS)
    assert snapshot.pop("checked_at")
    assert snapshot.pop("oldest_pending_created_age_seconds") is None
    assert set(snapshot.values()) == {0}


async def test_aggregate_matches_dispatch_rules_and_lease_boundaries(database, actors):
    async with database() as db:
        now = await db.scalar(select(func.current_timestamp()))
        future, past = now + timedelta(days=1), now - timedelta(seconds=1)
        rows = [
            attempt(actors[0][0], now),
            attempt(
                actors[0][0],
                now,
                vision_lease_token=uuid.uuid4(),
                vision_lease_until=future,
                vision_attempts=1,
            ),
            # The second live model call has used its budget but is not awaiting failure completion.
            attempt(
                actors[0][0],
                now,
                vision_lease_token=uuid.uuid4(),
                vision_lease_until=future,
                vision_attempts=2,
            ),
            attempt(
                actors[0][0],
                now,
                vision_lease_token=uuid.uuid4(),
                vision_lease_until=now,
                vision_attempts=1,
            ),
            attempt(
                actors[0][0],
                now,
                vision_lease_token=uuid.uuid4(),
                vision_lease_until=past,
                vision_attempts=2,
            ),
            attempt(actors[0][0], now, vision_attempts=2),
            attempt(actors[0][0], now, vision_available_at=future),
            attempt(actors[0][0], now, vision_dispatch_after=future),
            attempt(actors[0][0], now, vision_attempts=2, vision_available_at=future),
            attempt(actors[0][0], now, status="VERIFIED"),
            attempt(actors[0][0], now, status="REJECTED", vision_dispatch_after=future),
            attempt(actors[0][0], now, status="FAILED"),
            attempt(actors[0][0], now, status="VERIFIED", photo_redacted_at=now),
            attempt(actors[0][0], now, status="PENDING_UPLOAD", created_at=now - timedelta(days=7)),
        ]
        db.add_all(rows)
        await db.flush()
        snapshot = await probe_module().backlog_snapshot(db, max_attempts=jobs.MAX_ATTEMPTS)
        assert snapshot == {
            "checked_at": now.isoformat(),
            "pending_count": 9,
            "active_lease_count": 2,
            "expired_lease_count": 2,
            "exhausted_awaiting_completion_count": 3,
            "pending_dispatch_due_count": 4,
            "cleanup_pending_count": 3,
            "cleanup_blocked_count": 0,
            "cleanup_dispatch_due_count": 2,
            "dispatch_due_count": 6,
            "oldest_pending_created_age_seconds": 600.0,
        }
        selected = await repo.due_dispatches(db, now, limit=100)
        assert {row.id for row in selected} == {rows[i].id for i in (0, 3, 4, 5, 9, 11)}
        assert len(selected) == snapshot["dispatch_due_count"]
        assert all(str(row.id) not in json.dumps(snapshot) for row in rows)
        assert "private" not in json.dumps(snapshot)


async def test_blocked_cleanup_is_visible_but_never_counted_as_dispatch_due(database, actors):
    async with database() as db:
        now = await db.scalar(select(func.current_timestamp()))
        rows = [
            attempt(
                actors[0][0], now, status=status, vision_retry_reason=PHOTO_CLEANUP_BLOCKED_REASON
            )
            for status in repo.TERMINAL
        ]
        rows += [
            attempt(actors[0][0], now, status="FAILED"),
            attempt(actors[0][0], now, status="FAILED", vision_retry_reason="storage_unavailable"),
            attempt(
                actors[0][0],
                now,
                status="FAILED",
                photo_redacted_at=now,
                vision_retry_reason=PHOTO_CLEANUP_BLOCKED_REASON,
            ),
            attempt(actors[0][0], now),
        ]
        db.add_all(rows)
        await db.flush()
        snapshot = await probe_module().backlog_snapshot(db)
        assert snapshot["cleanup_pending_count"] == 5
        assert snapshot["cleanup_blocked_count"] == 3
        assert snapshot["cleanup_dispatch_due_count"] == 2
        assert snapshot["dispatch_due_count"] == 3
        selected = await repo.due_dispatches(db, now, limit=100)
        assert {row.id for row in selected} == {rows[i].id for i in (3, 4, 6)}


async def test_probe_uses_read_only_snapshot_and_does_not_change_or_lock_attempts(database, actors):
    engine = database.kw["bind"]
    async with database() as db:
        now = await db.scalar(select(func.current_timestamp()))
        row = attempt(actors[0][0], now)
        db.add(row)
        await db.commit()
        before = (await db.execute(select(TerritoryAttempt.__table__))).mappings().all()
        # A read-only inventory must still finish while another transaction holds a row lock.
        await db.get(TerritoryAttempt, row.id, with_for_update=True)
        statements = []
        probe = probe_module()

        def record(_conn, _cursor, statement, *_args):
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            result = await probe.database_inventory(engine=engine)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", record)
        assert result["lease_columns_present"] is True
        assert result["attempt_counts"] == [{"status": "VISION_PENDING", "count": 1}]
        assert result["backlog"]["dispatch_due_count"] == 1
        assert result["backlog"]["oldest_pending_created_age_seconds"] >= 600
        assert statements[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        assert statements[1] == "SET LOCAL statement_timeout = '5000'"
        assert all("FOR UPDATE" not in sql.upper() for sql in statements)
        after = (await db.execute(select(TerritoryAttempt.__table__))).mappings().all()
        assert before == after
    # The probe does not dispose an engine supplied by its caller.
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1


async def test_missing_lease_migration_reports_unknown_backlog(database):
    async with database() as db:
        await db.execute(text("ALTER TABLE territory_attempts DROP COLUMN vision_retry_reason"))
        await db.commit()
    result = await probe_module().database_inventory(engine=database.kw["bind"])
    assert result == {"lease_columns_present": False, "attempt_counts": [], "backlog": None}


async def test_standalone_probe_needs_no_new_worker_service_or_repository(database, monkeypatch):
    # Maintenance copies only the script into an existing worker before/after deployment.
    for module in (
        "daengs_backend.services.territory_vision_jobs",
        "daengs_backend.repositories.territory_vision",
    ):
        monkeypatch.setitem(sys.modules, module, None)
    result = await probe_module().database_inventory(engine=database.kw["bind"])
    assert result["lease_columns_present"] is True
    assert result["backlog"]["pending_count"] == 0


async def test_future_created_time_does_not_report_negative_age(database, actors):
    async with database() as db:
        now = await db.scalar(select(func.current_timestamp()))
        db.add(attempt(actors[0][0], now, created_at=now + timedelta(seconds=1)))
        await db.flush()
        result = await probe_module().backlog_snapshot(db, max_attempts=jobs.MAX_ATTEMPTS)
    assert result["oldest_pending_created_age_seconds"] == 0


async def test_inventory_does_not_mix_snapshots_during_concurrent_change(
    database, actors, monkeypatch
):
    engine = database.kw["bind"]
    async with database() as db:
        now = await db.scalar(select(func.current_timestamp()))
        row = attempt(actors[0][0], now)
        db.add(row)
        await db.commit()
    probe = probe_module()
    original = probe.backlog_snapshot

    async def changed_after_status_counts(connection, **kwargs):
        async with database() as db:
            stored = await db.get(TerritoryAttempt, row.id)
            stored.status = "FAILED"
            stored.vision_model = stored.vision_model_version = "test"
            stored.photo_redacted_at = now
            await db.commit()
        return await original(connection, **kwargs)

    monkeypatch.setattr(probe, "backlog_snapshot", changed_after_status_counts)
    result = await probe.database_inventory(engine=engine)
    assert result["attempt_counts"] == [{"status": "VISION_PENDING", "count": 1}]
    assert result["backlog"]["pending_count"] == 1
    async with database() as db:
        assert (await db.get(TerritoryAttempt, row.id)).status == "FAILED"
