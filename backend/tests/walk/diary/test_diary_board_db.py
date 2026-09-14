"""New JSONB receipts/leases use the existing table and owner lifecycle."""

import asyncio
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, text

from daengs_backend.config import settings
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.models.walk_entry_context import WalkEntryContextEnvelope, WalkEntryContextJob
from daengs_backend.models.walk_storyboard import WalkStoryboard
from daengs_backend.schemas.walk_storyboard import StoryboardRequest
from daengs_backend.services import walk_diary_slot_writing as writing
from daengs_backend.services.walk_diary_board_slot_writing import (
    write_board,
    write_legacy_slot_board,
)
from daengs_backend.services.walk_diary_generation import generate_diary, get_diary
from daengs_backend.services.walk_storyboard_state import StoryboardConflict
from daengs_walk.diary_board_output import BOARD_FORMAT
from daengs_walk.diary_input import digest
from tests.walk.support.diary import place_payload
from tests.walk.support.entry_v2 import AT, ENTRY, OWNER, WALK
from tests.walk.support.paths import REPO
from tests.walk.support.photo_input import context_envelope, entry


@pytest.fixture
async def board_database(photo_database, monkeypatch):
    monkeypatch.setattr(settings, "walk_diary_enabled", True)
    monkeypatch.setattr(settings, "walk_entry_context_enabled", False)
    async with photo_database() as db:
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        await raw.execute((REPO / "db/init/20_walk_storyboards.sql").read_text(encoding="utf-8"))
        await db.commit()
    return photo_database


def spec(**updates):
    return StoryboardRequest.model_validate(
        {"bundle_format": BOARD_FORMAT, "expected_entries": {}, "target_scene_count": 3, **updates}
    )


@pytest.mark.parametrize("cancelled", [False, True])
async def test_legacy_collection_rechecks_concurrent_reservation(
    board_database, monkeypatch, cancelled
):
    factory = board_database
    monkeypatch.setattr(settings, "walk_diary_space_enabled", True)
    outer_writer = AsyncMock(side_effect=AssertionError("another request already reserved"))
    other_writer = AsyncMock(
        side_effect=asyncio.CancelledError() if cancelled else RuntimeError("provider unavailable")
    )
    async with factory() as db:

        async def collect(_board):
            assert not db.in_transaction()
            # A second connection must be able to acquire the Walk lock and reserve
            # while legacy acquisition is in flight, before the outer writer starts.
            async with factory() as other:
                if cancelled:
                    with pytest.raises(asyncio.CancelledError):
                        await generate_diary(other, OWNER, WALK, spec(), writer=other_writer)
                else:
                    completed = await generate_diary(
                        other, OWNER, WALK, spec(), writer=other_writer
                    )
                    assert completed.status == "ready"

        collected = AsyncMock(side_effect=collect)
        response = await generate_diary(
            db, OWNER, WALK, spec(), writer=outer_writer, legacy_collector=collected
        )
        assert not db.in_transaction()
    assert response.status == ("running" if cancelled else "ready")
    assert response.generation == 1
    outer_writer.assert_not_awaited()
    other_writer.assert_awaited_once()
    collected.assert_awaited_once()
    async with factory() as db:
        assert await get_diary(db, OWNER, WALK, 3, BOARD_FORMAT) == response


async def test_receipt_survives_connection_policy_change_and_owner_deletion(
    board_database, monkeypatch
):
    factory = board_database

    async def fail(source, prepared):
        raise RuntimeError("provider unavailable")

    async with factory() as db:
        first = await generate_diary(db, OWNER, WALK, spec(), writer=fail)
    assert first.status == "ready" and first.bundle.model_status == "unavailable"
    assert len(first.bundle.scenes) >= 2 and all(s.body for s in first.bundle.scenes)
    async with factory() as db:
        stored = deepcopy((await db.get(WalkStoryboard, WALK)).bundle)
    assert stored["format"] == "walk-diary-board-storage-v2"
    assert all(not s["evidence"] for s in stored["writing_receipt"]["scenes"])
    monkeypatch.setattr(writing, "MODEL", "later-policy")
    async with factory() as db:
        retained = await get_diary(db, OWNER, WALK, 5, BOARD_FORMAT)
    assert retained.bundle == first.bundle and retained.target_scene_count == 3
    async with factory() as db:
        with pytest.raises(StoryboardConflict):
            await generate_diary(db, OWNER, WALK, spec(bundle_format="walk-diary-bundle-v1"))
    async with factory() as db:
        assert (await db.get(WalkStoryboard, WALK)).bundle == stored
        await db.execute(text("DELETE FROM app_users WHERE id=:id"), {"id": OWNER})
        await db.commit()
        assert await db.scalar(text("SELECT count(*) FROM walk_storyboards")) == 0


async def test_cancelled_new_lease_stays_typed_and_recovers_after_expiry(board_database):
    factory = board_database

    async with factory() as db:

        async def cancel(source, prepared):
            assert not db.in_transaction()
            async with factory() as other:
                marker = (await other.get(WalkStoryboard, WALK)).bundle
                assert marker["bundle_format"] == BOARD_FORMAT
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await generate_diary(db, OWNER, WALK, spec(), writer=cancel)
    async with factory() as db:
        row = await db.get(WalkStoryboard, WALK)
        assert row.status == "running"
        row.updated_at = datetime.now(UTC) - timedelta(seconds=61)
        await db.commit()
    async with factory() as db:

        async def write(source, prepared):
            return await write_board(source, prepared)

        recovered = await generate_diary(db, OWNER, WALK, spec(), writer=write)
    assert recovered.status == "ready" and recovered.generation == 2


async def test_publication_deadline_survives_disconnect_and_get_wins_over_late_writer(
    board_database,
):
    factory = board_database
    entered, release = asyncio.Event(), asyncio.Event()

    async def write(source, prepared):
        entered.set()
        await release.wait()
        return await write_board(source, prepared)

    async def generate():
        async with factory() as db:
            return await generate_diary(
                db, OWNER, WALK, spec(preparation_budget_ms=10000), writer=write
            )

    task = asyncio.create_task(generate())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        async with factory() as db:
            row = await db.get(WalkStoryboard, WALK)
            marker = deepcopy(row.bundle)
            assert marker["format"] == "walk-diary-preparation-v1"
            marker["deadline_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            row.bundle = marker
            await db.commit()
        async with factory() as db:
            published = await get_diary(db, OWNER, WALK, 3, BOARD_FORMAT)
        assert published.status == "ready" and published.generation == 1
        assert published.bundle.model_dump(mode="json") == marker["fallback"]["bundle"]
        release.set()
        late = await asyncio.wait_for(task, 10)
        assert late.bundle == published.bundle and late.generation == published.generation
        async with factory() as db:
            repeated = await generate_diary(
                db, OWNER, WALK, spec(preparation_budget_ms=10000), writer=write
            )
        assert repeated.bundle == published.bundle and repeated.generation == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_cancelled_publication_is_recovered_from_saved_jsonb_on_another_connection(
    board_database,
):
    factory = board_database

    async def cancel(*_):
        raise asyncio.CancelledError

    async with factory() as db:
        with pytest.raises(asyncio.CancelledError):
            await generate_diary(db, OWNER, WALK, spec(preparation_budget_ms=10000), writer=cancel)
    async with factory() as db:
        row = await db.get(WalkStoryboard, WALK)
        marker = deepcopy(row.bundle)
        marker["deadline_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        row.bundle = marker
        await db.commit()
    async with factory() as db:
        result = await get_diary(db, OWNER, WALK, 3, BOARD_FORMAT)
    assert result.status == "ready" and result.generation == 1
    assert result.bundle.model_dump(mode="json") == marker["fallback"]["bundle"]


async def save_context_record(factory):
    payload = entry().payload
    payload["recorded_at"] = payload["location"]["captured_at"] = AT.isoformat()
    envelope = context_envelope()
    envelope["target"].update(
        walk_id=str(WALK),
        id=str(ENTRY),
        revision=1,
        event_at=AT.isoformat(),
        location=payload["location"],
    )
    facts = place_payload(("registered-park", "합성 공원", 60))
    envelope.update(status="known", payload=facts, payload_sha256=digest(facts))
    job_id = uuid.uuid4()
    async with factory() as db:
        db.add(
            WalkEntry(walk_id=WALK, id=ENTRY, revision=1, mutation_id=uuid.uuid4(), payload=payload)
        )
        await db.flush()
        db.add(
            WalkEntryContextJob(
                id=job_id,
                walk_id=WALK,
                entry_id=ENTRY,
                revision=1,
                policy_version="walk-entry-context-v1",
                tag="space.facility",
                state="completed",
                attempts=1,
                available_at=AT,
            )
        )
        await db.flush()
        db.add(
            WalkEntryContextEnvelope(
                id=uuid.uuid4(),
                job_id=job_id,
                attempt=1,
                created_at=AT,
                envelope=envelope,
            )
        )
        await db.commit()


async def cited_prose(payload, schema):
    return {
        "scenes": [
            {
                "scene_id": s["scene_id"],
                "text": "가까이에 등록된 공원이 있었다.",
                "evidence_ids": [s["scene"]["where"][0]["id"]],
                "action_id": s["action"]["id"] if s["action"] else None,
            }
            for s in payload["scenes"]
        ]
    }


async def test_cited_facts_round_trip_and_survive_context_loss_without_regeneration(
    board_database,
    monkeypatch,
):
    factory = board_database
    monkeypatch.setattr(settings, "walk_entry_context_enabled", True)
    await save_context_record(factory)
    calls = 0

    async def write(source, base):
        nonlocal calls
        calls += 1
        return await write_legacy_slot_board(source, base, cited_prose)

    request = spec(expected_entries={str(ENTRY): 1})
    async with factory() as db:
        first = await generate_diary(db, OWNER, WALK, request, writer=write)
    assert first.bundle.model_status == "accepted"
    async with factory() as db:
        saved = deepcopy((await db.get(WalkStoryboard, WALK)).bundle)
        assert sum(len(s["evidence"]) for s in saved["writing_receipt"]["scenes"]) == 1
        await db.execute(delete(WalkEntryContextEnvelope))
        await db.commit()
    monkeypatch.setattr(writing, "MODEL", "future-writer")
    async with factory() as db:
        second = await get_diary(db, OWNER, WALK, 3, BOARD_FORMAT)
        assert second.bundle == first.bundle and second.background_update_available
    async with factory() as db:
        repeated = await generate_diary(db, OWNER, WALK, request, writer=write)
        assert repeated.bundle == first.bundle and repeated.generation == first.generation
        assert (await db.get(WalkStoryboard, WALK)).bundle == saved
    assert calls == 1
    assert "writing_receipt" not in first.model_dump_json()


async def test_late_cited_result_cannot_replace_new_original_and_its_receipt(
    board_database, monkeypatch
):
    factory = board_database
    monkeypatch.setattr(settings, "walk_entry_context_enabled", True)
    await save_context_record(factory)
    entered, release = asyncio.Event(), asyncio.Event()

    async def call(request, *, wait=False):
        async with factory() as db:

            async def write(source, base):
                assert not db.in_transaction()
                if wait:
                    entered.set()
                    await release.wait()
                return await write_legacy_slot_board(source, base, cited_prose)

            return await generate_diary(db, OWNER, WALK, request, writer=write)

    old = asyncio.create_task(call(spec(expected_entries={str(ENTRY): 1}), wait=True))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        async with factory() as db:
            record = await db.get(WalkEntry, (WALK, ENTRY))
            record.revision = 2
            record.mutation_id = uuid.uuid4()
            record.payload = {**record.payload, "note": "새로 고친 원문"}
            row = await db.get(WalkStoryboard, WALK)
            row.updated_at = datetime.now(UTC) - timedelta(seconds=61)
            await db.commit()
        fresh = await asyncio.wait_for(call(spec(expected_entries={str(ENTRY): 2})), 10)
        assert fresh.status == "ready" and fresh.generation == 2
        assert any(s.body == "새로 고친 원문" for s in fresh.bundle.scenes)
        async with factory() as db:
            saved = deepcopy((await db.get(WalkStoryboard, WALK)).bundle)
        release.set()
        late = await asyncio.wait_for(old, 10)
        assert late.bundle == fresh.bundle and late.generation == 2
        async with factory() as db:
            assert (await db.get(WalkStoryboard, WALK)).bundle == saved
    finally:
        release.set()
        if not old.done():
            old.cancel()
        await asyncio.gather(old, return_exceptions=True)
