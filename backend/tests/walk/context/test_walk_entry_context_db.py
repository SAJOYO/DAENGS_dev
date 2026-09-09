"""Real migration/queue/rollback checks, only in an explicitly configured disposable local DB."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.models.walk_entry_context import WalkEntryContextEnvelope, WalkEntryContextJob
from daengs_backend.repositories import walk_entry_context as repo
from daengs_backend.services import walk_entry_context as service
from daengs_backend.services.walk_entry_context_source import Collected
from tests.walk.support.paths import REPO as REPO_ROOT

ROOT = REPO_ROOT
NOW = datetime.now(UTC)


@pytest.fixture
async def database():
    address = os.environ.get("WALK_CONTEXT_TEST_DATABASE_URL")
    if not address:
        pytest.skip("WALK_CONTEXT_TEST_DATABASE_URL: disposable localhost DB not configured")
    url = make_url(address)
    if url.host not in {"localhost", "127.0.0.1"} or url.database != "walk_context_test":
        pytest.fail("only localhost/walk_context_test is allowed")
    schema = "walk_context_" + uuid.uuid4().hex
    admin = create_async_engine(url, poolclass=NullPool)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema, "lock_timeout": "3000"}},
    )
    try:
        async with engine.begin() as connection:
            raw = (await connection.get_raw_connection()).driver_connection
            # Minimal external parent; actual entry/context schema files are the test subject.
            await raw.execute("CREATE TABLE walks (id UUID PRIMARY KEY)")
            await raw.execute((ROOT / "db/init/19_walk_entries.sql").read_text(encoding="utf-8"))
            migration = (ROOT / "db/migrations/2026-09-08_walk_entry_contexts.sql").read_text(
                encoding="utf-8"
            )
            await raw.execute(migration)
            await raw.execute(migration)
            await raw.execute(
                (ROOT / "db/init/24_walk_entry_contexts.sql").read_text(encoding="utf-8")
            )
            await raw.execute(
                (ROOT / "db/migrations/verify_2026-09-08_walk_entry_contexts.sql").read_text(
                    encoding="utf-8"
                )
            )
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def seed(factory):
    walk, entry = uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        await db.execute(text("INSERT INTO walks(id) VALUES (:id)"), {"id": walk})
        row = WalkEntry(
            walk_id=walk,
            id=entry,
            revision=1,
            mutation_id=uuid.uuid4(),
            payload={
                "recorded_at": NOW.isoformat(),
                "kind": "note",
                "note": "원문",
                "location": {"lat": 37.5, "lng": 127},
            },
        )
        db.add(row)
        await repo.enqueue(db, row, NOW)
        await repo.enqueue(db, row, NOW)
        await db.commit()
    return walk, entry


@pytest.mark.parametrize("sql_null", [False, True])
async def test_durable_enqueue_idempotency_rollback_and_delete_purge(database, sql_null):
    walk, entry = await seed(database)
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(WalkEntryContextJob)) == 4
        row = await db.get(WalkEntry, (walk, entry))
        row.revision = 2
        await repo.enqueue(db, row, NOW)
        await db.rollback()
    ticket = await service.take(database)
    assert await service.finish(database, ticket, Collected("empty", payload={"items": []}))
    async with database() as db:
        row = await db.get(WalkEntry, (walk, entry))
        assert row.revision == 1
        assert await db.scalar(select(func.count()).select_from(WalkEntryContextEnvelope)) == 1
        if sql_null:
            await db.execute(
                text("UPDATE walk_entries SET payload = NULL WHERE walk_id = :walk"), {"walk": walk}
            )
        else:
            row.payload = None
        await db.commit()
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(WalkEntryContextJob)) == 0
        assert await db.scalar(select(func.count()).select_from(WalkEntryContextEnvelope)) == 0


async def test_lease_recovery_and_revision_fence(database):
    walk, entry = await seed(database)
    ticket = await service.take(database)
    async with database() as db:
        job = await db.get(WalkEntryContextJob, ticket["id"])
        job.lease_until = NOW - timedelta(seconds=1)
        # Complete other slots so the expired job is the only claimable item.
        for other in await db.scalars(
            select(WalkEntryContextJob).where(WalkEntryContextJob.id != job.id)
        ):
            other.state = "completed"
        await db.commit()
    recovered = await service.take(database)
    assert recovered["id"] == ticket["id"] and recovered["token"] != ticket["token"]
    assert not await service.finish(database, ticket, Collected("empty", payload={}))
    async with database() as db:
        row = await db.get(WalkEntry, (walk, entry))
        row.revision = 2
        await repo.enqueue(db, row, NOW)
        await db.commit()
    assert not await service.finish(database, recovered, Collected("empty", payload={}))
    async with database() as db:
        row = await db.get(WalkEntry, (walk, entry))
        jobs, latest = await repo.current(db, row)
        assert len(jobs) == 4 and not latest and all(j.revision == 2 for j in jobs)


async def test_skip_locked_claims_another_job(database):
    await seed(database)
    async with database() as first, database() as second:
        a = await repo.claim(first, NOW + timedelta(seconds=1))
        b = await repo.claim(second, NOW + timedelta(seconds=1))
        assert a.id != b.id
        await first.rollback()
        await second.rollback()


async def test_pin_policy_is_idempotent_isolated_and_read_flag_gated(database, monkeypatch):
    from daengs_backend.config import settings

    walk, entry = await seed(database)
    async with database() as db:
        row = await db.get(WalkEntry, (walk, entry))
        await repo.enqueue(db, row, NOW, policy=repo.PIN_POLICY)
        await repo.enqueue(db, row, NOW, policy=repo.PIN_POLICY)
        legacy, _ = await repo.current(db, row)
        current, envelopes = await repo.current(db, row, policy=repo.PIN_POLICY)
        assert len(legacy) == len(current) == 4 and not envelopes
        assert {job.id for job in legacy}.isdisjoint(job.id for job in current)
        for job in legacy:
            job.state = "completed"
        await db.commit()
    async with database() as db:
        monkeypatch.setattr(settings, "walk_entry_v2_enabled", False)
        assert await repo.claim(db, NOW + timedelta(seconds=1)) is None
        monkeypatch.setattr(settings, "walk_entry_v2_enabled", True)
        claimed = await repo.claim(db, NOW + timedelta(seconds=1))
        assert claimed.policy_version == repo.PIN_POLICY and claimed.attempts == 1
        await db.rollback()
