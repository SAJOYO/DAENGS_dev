"""Real PostgreSQL maintenance: preserve history and user boards; fence bounded requeues."""

import asyncio
import copy
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select, text

from daengs_backend.config import settings
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.models.walk_entry_context import WalkEntryContextEnvelope as Envelope
from daengs_backend.models.walk_entry_context import WalkEntryContextJob as Job
from daengs_backend.models.walk_entry_v2 import WalkEntryPin as Pin
from daengs_backend.models.walk_storyboard import WalkStoryboard
from daengs_backend.repositories import walk_catalog_demand as demand
from daengs_backend.repositories import walk_entry_context as queue
from daengs_backend.services import walk_context_backfill as backfill
from daengs_backend.services import walk_entry_context as context
from daengs_backend.services.walk_background.contracts import Collected
from tests.walk.context.test_walk_catalog_demand_db import catalog_database  # noqa: F401
from tests.walk.context.test_walk_entry_context_db import database as local_database  # noqa: F401
from tests.walk.context.test_walk_entry_context_db import seed
from tests.walk.support.paths import REPO

NOW = datetime.now(UTC)


@pytest.fixture
async def database(catalog_database, monkeypatch):  # noqa: F811
    async with catalog_database() as db:
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        await raw.execute((REPO / "db/init/20_walk_storyboards.sql").read_text(encoding="utf-8"))
        await db.commit()
    monkeypatch.setattr(settings, "walk_entry_context_enabled", True)
    monkeypatch.setattr(backfill, "automatic_ready", lambda: True)
    monkeypatch.setattr(settings, "walk_sgis_key", SecretStr("test-only"))
    monkeypatch.setattr(settings, "walk_sgis_secret", SecretStr("test-only"))
    return catalog_database


async def terminal(factory, *, statuses=None):
    walk, entry = await seed(factory)
    statuses = statuses or {tag: "not_requested" for tag in backfill.TAGS}
    async with factory() as db:
        for job in await db.scalars(select(Job).where(Job.walk_id == walk)):
            job.state = "completed"
            if job.tag in statuses:
                job.attempts = 3 if statuses[job.tag] == "unavailable" else 1
                if job.attempts == 3:
                    job.state = "failed"
                db.add(
                    Envelope(
                        id=uuid.uuid4(),
                        job_id=job.id,
                        attempt=job.attempts,
                        created_at=NOW,
                        envelope={"status": statuses[job.tag]},
                    )
                )
        await db.commit()
    return walk, entry


async def apply(factory, walks):
    preview = await backfill.run(factory, walk_ids=walks)
    result = await backfill.run(
        factory, walk_ids=walks, apply=True, expected_plan=preview["plan_digest"]
    )
    return preview, result


async def test_migration_preserves_old_round_and_current_read_changes_only_after_new_result(
    database,
):
    factory = database
    walk, entry = await terminal(
        factory,
        statuses={
            "space.address": "unavailable",
            "space.park": "partial",
            "space.commerce": "empty",
            "space.river": "known",
        },
    )
    async with factory() as db:
        original = copy.deepcopy((await db.get(WalkEntry, (walk, entry))).payload)
        old = await db.scalar(select(Envelope).join(Job).where(Job.tag == "space.address"))
        old_id, old_bytes = old.id, copy.deepcopy(old.envelope)
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        # Recreate the exact pre-upgrade layout WITH saved data, then upgrade in place.
        await raw.execute("""
            ALTER TABLE walk_entry_context_envelopes DROP COLUMN collection_round CASCADE;
            ALTER TABLE walk_entry_context_jobs DROP COLUMN collection_round, DROP COLUMN backfill_policy;
            ALTER TABLE walk_entry_context_envelopes ADD UNIQUE (job_id, attempt);
        """)
        for path in ["db/migrations/2026-09-10_walk_context_recollection.sql"] * 2 + [
            "db/init/32_walk_context_recollection.sql",
            "db/migrations/verify_2026-09-10_walk_context_recollection.sql",
        ]:
            await raw.execute((REPO / path).read_text(encoding="utf-8"))
        await db.commit()
    preview, applied = await apply(factory, [walk])
    assert preview["eligible_sources"] == applied["scheduled_sources"] == 1
    assert preview["source_reasons"]["usable"] == 3
    ticket = await context.take(factory)
    assert ticket["collection_round"] == 1 and ticket["attempt"] == 1
    assert await context.finish(factory, ticket, Collected("known", payload={"test": "new"}))
    async with factory() as db:
        row = await db.get(WalkEntry, (walk, entry))
        jobs, latest = await queue.current(db, row)
        address = next(j for j in jobs if j.tag == "space.address")
        assert latest[address.id].collection_round == 1
        assert latest[address.id].envelope["status"] == "known"
        assert (await db.get(Envelope, old_id)).envelope == old_bytes
        assert row.payload == original
    again = await backfill.run(factory, walk_ids=[walk])
    assert again["eligible_sources"] == 0 and again["source_reasons"]["already_requested"] == 1


async def test_missing_jobs_created_once_and_concurrent_plan_is_fenced(database):
    walk, _ = await terminal(database)
    async with database() as db:
        await db.execute(delete(Job).where(Job.walk_id == walk))
        await db.commit()
    preview = await backfill.run(database, walk_ids=[walk])
    assert preview["source_reasons"] == {"missing_job": 4}
    results = await asyncio.gather(
        *[
            backfill.run(
                database, walk_ids=[walk], apply=True, expected_plan=preview["plan_digest"]
            )
            for _ in range(2)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(r, backfill.StaleBackfillPlan) for r in results) == 1
    assert next(r for r in results if isinstance(r, dict))["scheduled_sources"] == 4
    async with database() as db:
        jobs = list(await db.scalars(select(Job)))
        assert len(jobs) == 4 and all(j.backfill_policy == backfill.BACKFILL_POLICY for j in jobs)
        assert not list(await db.scalars(select(Envelope)))


@pytest.mark.parametrize("change", ["revision", "delete", "board", "pin"])
async def test_changed_preview_rolls_back_without_scheduling(database, change):
    walk, entry = await terminal(database)
    before = await backfill.run(database, walk_ids=[walk])
    async with database() as db:
        row = await db.get(WalkEntry, (walk, entry))
        if change == "revision":
            row.revision += 1
        elif change == "delete":
            row.payload = None
        elif change == "pin":
            db.add(
                Pin(
                    walk_id=walk,
                    entry_id=entry,
                    pin_revision=1,
                    payload={"state": "resolved", "point": {"lat": 37.6, "lng": 127}},
                )
            )
        else:
            db.add(
                WalkStoryboard(
                    walk_id=walk,
                    generation=1,
                    input_revision="a" * 64,
                    status="ready",
                    updated_at=NOW,
                    bundle={"user": "keep exactly"},
                )
            )
        await db.commit()
    with pytest.raises(backfill.StaleBackfillPlan):
        await backfill.run(
            database, walk_ids=[walk], apply=True, expected_plan=before["plan_digest"]
        )
    async with database() as db:
        assert all(j.backfill_policy is None for j in await db.scalars(select(Job)))


@pytest.mark.parametrize("state", ["running", "ready", "failed"])
async def test_stored_board_never_changes_or_invalidates(database, state):
    walk, _ = await terminal(database)
    saved = {"text": "사람이 고친 보드", "nested": [1, 2]}
    async with database() as db:
        db.add(
            WalkStoryboard(
                walk_id=walk,
                generation=7,
                input_revision="a" * 64,
                status=state,
                updated_at=NOW,
                bundle=saved,
            )
        )
        await db.commit()
    preview, result = await apply(database, [walk])
    assert preview["skipped"] == {"stored_board": 1}
    assert result["scheduled_sources"] == 0
    async with database() as db:
        board = await db.get(WalkStoryboard, walk)
        assert board.bundle == saved and board.generation == 7 and board.status == state


@pytest.mark.parametrize(
    "kind", ["deleted", "provisional_pin", "no_location", "unsupported_location"]
)
async def test_unusable_location_is_never_invented(database, kind):
    walk, entry = await terminal(database)
    async with database() as db:
        row = await db.get(WalkEntry, (walk, entry))
        if kind == "deleted":
            row.payload = None
        elif kind == "no_location":
            row.payload = dict(row.payload, location=None)
        elif kind == "unsupported_location":
            row.payload = dict(row.payload, location={"lat": 1, "lng": 1})
        else:
            db.add(
                Pin(
                    walk_id=walk,
                    entry_id=entry,
                    pin_revision=1,
                    payload={"state": "provisional", "point": {"lat": 37.5, "lng": 127}},
                )
            )
        await db.commit()
    result = await backfill.run(database, walk_ids=[walk])
    assert result["eligible_sources"] == 0 and result["skipped"] == {kind: 1}


async def test_only_current_policy_and_terminal_missing_sources_are_requested(database):
    walk, entry = await terminal(database)
    async with database() as db:
        row = await db.get(WalkEntry, (walk, entry))
        row.revision = 2
        db.add(Pin(walk_id=walk, entry_id=entry, pin_revision=1, payload=None))
        await db.flush()
        await queue.enqueue(db, row, NOW, policy=queue.PIN_POLICY)
        for job in await db.scalars(select(Job).where(Job.revision == 2)):
            if job.tag == "space.address":
                job.state = "cancelled"
            if job.tag == "space.commerce":
                job.state, job.attempts = "failed", 3
        await db.commit()
    preview, result = await apply(database, [walk])
    assert result["scheduled_sources"] == 1
    assert preview["source_reasons"] == {"cancelled": 1, "pending": 2, "missing_result": 1}
    async with database() as db:
        jobs = list(await db.scalars(select(Job)))
        recovered = [j for j in jobs if j.backfill_policy]
        assert len(recovered) == 1 and recovered[0].policy_version == queue.PIN_POLICY
        assert all(j.backfill_policy is None for j in jobs if j.revision == 1)


async def test_recollection_retains_three_attempt_ceiling_and_round_history(database):
    walk, _ = await terminal(database, statuses={"space.address": "unavailable"})
    await apply(database, [walk])
    for index in range(3):
        ticket = await context.take(database)
        assert ticket["collection_round"] == 1 and ticket["attempt"] == index + 1
        await context.finish(database, ticket, Collected("unavailable", retryable=True))
        async with database() as db:
            job = await db.get(Job, ticket["id"])
            job.available_at = NOW - timedelta(minutes=1)
            await db.commit()
    assert await context.take(database) is None
    async with database() as db:
        attempts = list(await db.scalars(select(Envelope)))
        assert sorted((e.collection_round, e.attempt) for e in attempts) == [
            (0, 3),
            (1, 1),
            (1, 2),
            (1, 3),
        ]
    assert (await backfill.run(database, walk_ids=[walk]))["eligible_sources"] == 0


async def test_queue_batch_limit_and_date_cursor(database):
    walk, entry = await terminal(database)
    async with database() as db:
        payload = (await db.get(WalkEntry, (walk, entry))).payload
        for _ in range(10):
            db.add(
                WalkEntry(
                    walk_id=walk,
                    id=uuid.uuid4(),
                    revision=1,
                    mutation_id=uuid.uuid4(),
                    payload=payload,
                )
            )
        await db.commit()
    preview, result = await apply(database, [walk])
    assert preview["eligible_sources"] == 44 and result["scheduled_sources"] == 40
    assert (await backfill.run(database, walk_ids=[walk]))["eligible_sources"] == 4
    second, _ = await terminal(database)
    window = {"since": NOW - timedelta(days=1), "until": NOW + timedelta(days=1), "limit": 1}
    page1 = await backfill.run(database, **window)
    page2 = await backfill.run(database, **window, after=uuid.UUID(page1["next_cursor"]))
    assert page1["has_more"] and page1["next_cursor"] == str(min(walk, second))
    assert page2["walk_count"] == 1 and not page2["has_more"]


async def test_apply_requires_reviewable_scope_and_exact_plan(database):
    with pytest.raises(ValueError):
        await backfill.run(database)
    with pytest.raises(ValueError):
        await backfill.run(database, walk_ids=[uuid.uuid4()], apply=True)
    with pytest.raises(ValueError):
        await backfill.run(database, walk_ids=[uuid.uuid4()] * 11, since=NOW)


async def test_previous_round_cannot_wake_new_attempt_with_the_same_number(database):
    walk, _ = await terminal(database, statuses={"space.address": "not_requested"})
    async with database() as db:
        old = await db.scalar(select(Envelope))
        old.envelope = {"status": "unavailable", "reason": "catalog_preparing"}
        await db.commit()
    await apply(database, [walk])
    ticket = await context.take(database)
    await context.finish(
        database, ticket, Collected("unavailable", "other_failure", retryable=True)
    )
    async with database() as db:
        job = await db.get(Job, ticket["id"])
        assert job.collection_round == 1 and job.attempts == 1
        assert await demand.wake_ready(db, [job.id], datetime.now(UTC)) == 0


async def test_missing_credentials_do_not_consume_the_one_time_request(database, monkeypatch):
    walk, _ = await terminal(database)
    monkeypatch.setattr(backfill, "automatic_ready", lambda: False)
    preview = await backfill.run(database, walk_ids=[walk])
    assert not preview["ready_to_apply"]
    with pytest.raises(ValueError, match="must be configured"):
        await backfill.run(
            database, walk_ids=[walk], apply=True, expected_plan=preview["plan_digest"]
        )
    async with database() as db:
        assert all(j.backfill_policy is None for j in await db.scalars(select(Job)))


async def test_registered_migration_verifier_rejects_each_schema_damage(database):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "backfill_migration_checks", REPO / "tools/check_migration_verification.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    date, name, fixture, table, mutations = next(
        case for case in module.CHECKS if case[1] == "walk_context_recollection"
    )
    migration = (REPO / f"db/migrations/{date}_{name}.sql").read_text(encoding="utf-8")
    verifier = (REPO / f"db/migrations/verify_{date}_{name}.sql").read_text(encoding="utf-8")
    for mutation in ["", f"DROP TABLE {table} CASCADE", *mutations]:
        async with database() as db:
            schema = "backfill_verify_" + uuid.uuid4().hex
            # asyncpg starts SQLAlchemy's transaction lazily; raw.execute alone bypasses it.
            await db.execute(text("SELECT 1"))
            raw = (await (await db.connection()).get_raw_connection()).driver_connection
            try:
                await raw.execute(f"CREATE SCHEMA {schema}; SET LOCAL search_path TO {schema};")
                await raw.execute(fixture + migration * 2)
                if mutation:
                    await raw.execute(mutation)
                    with pytest.raises(Exception, match="mismatch|missing table"):
                        await raw.execute(verifier)
                else:
                    await raw.execute(verifier)
            finally:
                await db.rollback()
