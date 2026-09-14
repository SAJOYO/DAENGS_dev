"""PostgreSQL: current-pin demand and waking only catalog-waiting current revisions."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from daengs_backend.config import settings
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.models.walk_entry_context import WalkEntryContextJob as Job
from daengs_backend.models.walk_entry_v2 import WalkEntryPin as Pin
from daengs_backend.repositories import walk_catalog_demand as demand
from daengs_backend.repositories import walk_entry_context as queue
from daengs_backend.services.walk_background.contracts import Collected
from daengs_backend.services.walk_records import context
from tests.walk.context.test_walk_entry_context_db import database as local_database  # noqa: F401
from tests.walk.context.test_walk_entry_context_db import seed
from tests.walk.support.paths import REPO


@pytest.fixture
async def catalog_database(local_database, monkeypatch):  # noqa: F811
    async with local_database() as db:
        await db.execute(
            text("ALTER TABLE walks ADD COLUMN started_at timestamptz NOT NULL DEFAULT now()")
        )
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        for name in ("25_walk_entry_pins.sql", "28_walk_commerce_context.sql"):
            await raw.execute((REPO / "db/init" / name).read_text(encoding="utf-8"))
        await db.commit()
    monkeypatch.setattr(settings, "walk_public_context_enabled", True)
    monkeypatch.setattr(settings, "walk_area_context_enabled", True)
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", True)
    return local_database


async def test_demand_uses_final_pin_and_excludes_retired_revision(catalog_database):
    factory = catalog_database
    walk, entry = await seed(factory)
    now = datetime.now(UTC)
    async with factory() as db:
        original = await demand.pending_and_recent(db, now)
        assert len(original) == 3
        assert all(row["point"] == {"lat": 37.5, "lng": 127} for row in original)
        row = await db.get(WalkEntry, (walk, entry))
        row.revision = 2
        db.add(
            Pin(
                walk_id=walk,
                entry_id=entry,
                pin_revision=1,
                payload={"state": "resolved", "point": {"lat": 37.66, "lng": 126.75}},
            )
        )
        await db.flush()
        await queue.enqueue(db, row, now, policy=queue.PIN_POLICY)
        await db.commit()
        current = await demand.pending_and_recent(db, now)
        assert len(current) == 3 and all(item["point"]["lat"] == 37.66 for item in current)
        pin = await db.get(Pin, (walk, entry))
        pin.payload = {"state": "provisional", "point": {"lat": 37.66, "lng": 126.75}}
        await db.commit()
        assert not await demand.pending_and_recent(db, now)


@pytest.mark.parametrize("changed", [False, True])
async def test_preparation_wait_is_expedited_only_for_live_pending_revision(
    catalog_database, changed
):
    factory = catalog_database
    walk, entry = await seed(factory)
    async with factory() as db:
        for job in await db.scalars(select(Job)):
            if job.tag != "space.commerce":
                job.state = "completed"
        await db.commit()
    ticket = await context.take(factory)
    assert ticket["tag"] == "space.commerce"
    before = datetime.now(UTC)
    await context.finish(
        factory, ticket, Collected("unavailable", "catalog_preparing", retryable=True)
    )
    async with factory() as db:
        job = await db.get(Job, ticket["id"])
        assert job.attempts == 1 and job.state == "pending"
        assert job.available_at >= before + timedelta(seconds=299)
        if changed:
            row = await db.get(WalkEntry, (walk, entry))
            row.revision += 1
            await queue.enqueue(db, row, datetime.now(UTC))
            await db.commit()
        assert await demand.wake_ready(db, [job.id], datetime.now(UTC)) == (0 if changed else 1)
        await db.commit()
        await db.refresh(job)
        if changed:
            assert job.state == "cancelled"
        else:
            assert job.available_at <= datetime.now(UTC)
    if not changed:
        next_ticket = await context.take(factory)
        assert next_ticket["attempt"] == 2
        await context.finish(factory, next_ticket, Collected("empty", payload={}))
        async with factory() as db:
            assert await demand.wake_ready(db, [ticket["id"]], datetime.now(UTC)) == 0
            assert (await db.get(Job, ticket["id"])).state == "completed"


async def test_old_completed_regions_are_not_renewed_but_pending_old_uploads_are(catalog_database):
    factory = catalog_database
    walk, _ = await seed(factory)
    async with factory() as db:
        await db.execute(
            text("UPDATE walks SET started_at = now() - interval '31 days' WHERE id=:id"),
            {"id": walk},
        )
        await db.commit()
        assert len(await demand.pending_and_recent(db, datetime.now(UTC))) == 3
        for job in await db.scalars(select(Job)):
            job.state = "completed"
        await db.commit()
        assert not await demand.pending_and_recent(db, datetime.now(UTC))
