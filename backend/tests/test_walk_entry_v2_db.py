"""Real PostgreSQL DDL, row locks, receipts, rollback and deletion; never the shared DB."""

import asyncio
import os
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from daengs_backend.config import settings
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.models.walk_entry_v2 import WalkEntryMutation, WalkEntryPin
from daengs_backend.schemas.walk_entry_v2 import EntryWriteV2, PinWrite
from daengs_backend.services import walk_entry_v2 as service
from tests.test_walk_entry_v2 import AT, OWNER, WALK, body, completion

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def database(monkeypatch):
    address = os.environ.get("WALK_PIN_TEST_DATABASE_URL")
    if not address:
        pytest.skip("WALK_PIN_TEST_DATABASE_URL: disposable local DB not configured")
    url = make_url(address)
    if url.host not in {"localhost", "127.0.0.1"} or url.database != "walk_pin_test":
        pytest.fail("only localhost/walk_pin_test is allowed")
    schema = "walk_pin_" + uuid.uuid4().hex
    admin = create_async_engine(url, poolclass=NullPool)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema, "lock_timeout": "5000"}},
    )
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", True)
    monkeypatch.setattr(settings, "walk_entry_v2_write_enabled", True)
    try:
        async with engine.begin() as connection:
            raw = (await connection.get_raw_connection()).driver_connection
            # Only external parent tables are minimal fixtures. Walk/entry/pin DDL is the real SQL.
            await raw.execute(
                "CREATE TABLE app_users (id UUID PRIMARY KEY); CREATE TABLE pets (id UUID PRIMARY KEY, app_user_id UUID REFERENCES app_users(id) ON DELETE CASCADE)"
            )
            for filename in [
                "db/init/06_walks.sql",
                "db/init/19_walk_entries.sql",
                "db/init/24_walk_entry_contexts.sql",
                "db/migrations/2026-09-09_walk_entry_pins.sql",
                "db/migrations/2026-09-09_walk_entry_pins.sql",
                "db/init/25_walk_entry_pins.sql",
                "db/migrations/verify_2026-09-09_walk_entry_pins.sql",
            ]:
                await raw.execute((ROOT / filename).read_text(encoding="utf-8"))
            await raw.execute("INSERT INTO app_users(id) VALUES ($1)", OWNER)
            await raw.execute(
                "INSERT INTO walks(id, app_user_id, client_session_id, started_at, ended_at) VALUES ($1,$2,$3,$4,$5)",
                WALK,
                OWNER,
                uuid.uuid4(),
                AT - timedelta(minutes=1),
                AT + timedelta(minutes=1),
            )
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def request():
    value = body()
    value["content"]["pet_id"] = None
    return EntryWriteV2.model_validate(value)


async def create(factory, entry_id, spec):
    async with factory() as db:
        return await service.write(db, OWNER, WALK, entry_id, spec)


async def count(db, table):
    return await db.scalar(select(func.count()).select_from(table))


async def test_receipt_history_exact_ack_and_pin_cas(database):
    entry_id, spec = uuid.uuid4(), request()
    original = await create(database, entry_id, spec)
    edit = EntryWriteV2(
        expected_revision=1,
        mutation_id=uuid.uuid4(),
        content=spec.content.model_copy(update={"behavior_code": "barking"}),
    )
    assert (await create(database, entry_id, edit))["revision"] == 2
    assert await create(database, entry_id, spec) == original
    pin_request = PinWrite.model_validate(completion(spec.model_dump(mode="json")))
    async with database() as db:
        with pytest.raises(service.EntryConflict):
            await service.finalize_pin(db, OWNER, WALK, entry_id, pin_request)
    pin_request.expected_revision = 2
    async with database() as db:
        final = await service.finalize_pin(db, OWNER, WALK, entry_id, pin_request)
        assert final["revision"] == 3 and final["pin_revision"] == 2
        assert final["content"]["behavior_code"] == "barking"
    async with database() as db:
        assert await service.finalize_pin(db, OWNER, WALK, entry_id, pin_request) == final
        assert await count(db, WalkEntryMutation) == 3
        assert await service.repo.contains_v2(db, [WALK])


@pytest.mark.parametrize("sql_null", [False, True])
async def test_delete_purges_receipts_and_pin_and_forbids_late_sql_writes(database, sql_null):
    entry_id, spec = uuid.uuid4(), request()
    await create(database, entry_id, spec)
    async with database() as db:
        if sql_null:
            await db.execute(
                text("UPDATE walk_entries SET payload = NULL WHERE id=:id"), {"id": entry_id}
            )
            await db.commit()
        else:
            result = await service.remove(db, OWNER, WALK, entry_id, 1, uuid.uuid4())
            assert set(result) == {"id", "revision", "mutation_id", "deleted"}
    async with database() as db:
        assert (await service.repo.pin(db, WALK, entry_id)).payload is None
        assert await count(db, WalkEntryMutation) == 0
        assert not await service.repo.contains_v2(db, [WALK])
        assert await service.repo.contains_v2(db, [WALK], entry_id=entry_id)
        with pytest.raises(service.EntryDeleted):
            await service.write(db, OWNER, WALK, entry_id, spec)
    async with database() as db:
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await db.execute(
                text("INSERT INTO walk_entry_mutations VALUES (:w,:e,:m,:h,'{}')"),
                {"w": WALK, "e": entry_id, "m": uuid.uuid4(), "h": "a" * 64},
            )
    async with database() as db:
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await db.execute(
                text("UPDATE walk_entry_pins SET payload='{}' WHERE entry_id=:e"), {"e": entry_id}
            )


async def test_create_failure_rolls_back_content_pin_and_receipt(database, monkeypatch):
    async with database() as db:
        monkeypatch.setattr(
            db, "commit", AsyncMock(side_effect=RuntimeError("simulated commit failure"))
        )
        with pytest.raises(RuntimeError):
            await service.write(db, OWNER, WALK, uuid.uuid4(), request())
        await db.rollback()
    async with database() as db:
        for table in (WalkEntry, WalkEntryPin, WalkEntryMutation):
            assert await count(db, table) == 0


async def test_concurrent_same_create_returns_one_ack_and_different_create_conflicts(database):
    spec, entry_id = request(), uuid.uuid4()
    responses = await asyncio.gather(
        create(database, entry_id, spec), create(database, entry_id, spec)
    )
    assert responses[0] == responses[1]
    other_id = uuid.uuid4()
    outcomes = await asyncio.gather(
        create(database, other_id, request()),
        create(database, other_id, request()),
        return_exceptions=True,
    )
    assert sum(isinstance(result, service.EntryConflict) for result in outcomes) == 1
    async with database() as db:
        assert await count(db, WalkEntry) == 2
        assert await count(db, WalkEntryMutation) == 2


async def test_delete_before_upload_survives_late_creation_and_owner_cascade(database):
    entry_id = uuid.uuid4()
    async with database() as db:
        await service.remove(db, OWNER, WALK, entry_id, 0, uuid.uuid4())
    with pytest.raises(service.EntryDeleted):
        await create(database, entry_id, request())
    await create(database, uuid.uuid4(), request())
    async with database() as db:
        await db.execute(text("DELETE FROM app_users WHERE id=:id"), {"id": OWNER})
        await db.commit()
        for table in (WalkEntry, WalkEntryPin, WalkEntryMutation):
            assert await count(db, table) == 0


async def test_concurrent_delete_and_pin_never_resurrect(database):
    entry_id, spec = uuid.uuid4(), request()
    await create(database, entry_id, spec)
    final = PinWrite.model_validate(completion(spec.model_dump(mode="json")))

    async def finish():
        async with database() as db:
            return await service.finalize_pin(db, OWNER, WALK, entry_id, final)

    async def delete():
        async with database() as db:
            return await service.remove(db, OWNER, WALK, entry_id, 1, uuid.uuid4())

    outcomes = await asyncio.gather(finish(), delete(), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in outcomes) == 1
    assert any(
        isinstance(result, (service.EntryConflict, service.EntryDeleted)) for result in outcomes
    )
    async with database() as db:
        row = await service.entries.get_entry(db, WALK, entry_id)
        await service.remove(db, OWNER, WALK, entry_id, row.revision, uuid.uuid4())
    async with database() as db:
        with pytest.raises(service.EntryDeleted):
            await service.finalize_pin(db, OWNER, WALK, entry_id, final)
        assert await count(db, WalkEntryMutation) == 0


async def test_repeatable_read_list_does_not_mix_before_and_after_commit(database):
    entry_id, spec = uuid.uuid4(), request()
    await create(database, entry_id, spec)
    async with database() as snapshot:
        await snapshot.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        first = await service.list_entries(snapshot, OWNER, WALK)
        final = PinWrite.model_validate(completion(spec.model_dump(mode="json")))
        async with database() as db:
            await service.finalize_pin(db, OWNER, WALK, entry_id, final)
        again = await service.list_entries(snapshot, OWNER, WALK)
        assert again == first
    async with database() as db:
        assert (await service.list_entries(db, OWNER, WALK))["revision"] != first["revision"]


async def test_raw_chunk_reference_uses_persisted_precision(database):
    from daengs_backend.models.walk import WalkPointChunk
    from daengs_backend.services.walk_chunk import encode_chunk
    from tests.test_walk_entry_v2 import located, pin, raw_point

    raw = raw_point(lat=37.5000004)
    async with database() as db:
        db.add(
            WalkPointChunk(
                walk_id=WALK, seq_from=0, seq_to=0, point_count=1, payload=encode_chunk([raw])
            )
        )
        await db.commit()
    spec = request().model_dump(mode="json")
    spec["content"]["location"] = {
        "lat": float(raw.lat),
        "lng": 127.0,
        "captured_at": raw.at.isoformat(),
        "accuracy_m": 5,
    }
    spec["pin"] = located(pin(state="resolved"), method="observed", raw=raw)
    spec["pin"]["reason"] = "direct_fix"
    result = await create(database, uuid.uuid4(), EntryWriteV2.model_validate(spec))
    assert result["pin"]["point"]["lat"] == float(raw.lat)
    spec["pin"]["source_refs"][0]["client_seq"] = 1
    with pytest.raises(service.EntryInvalid):
        await create(database, uuid.uuid4(), EntryWriteV2.model_validate(spec))
