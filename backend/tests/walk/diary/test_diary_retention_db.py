"""Persistent receipt upgrade and late context arrival against isolated PostgreSQL."""

from copy import deepcopy
from uuid import uuid4

from sqlalchemy import select, text

from daengs_backend.config import settings
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.models.walk_entry_context import WalkEntryContextEnvelope as Envelope
from daengs_backend.models.walk_entry_context import WalkEntryContextJob as Job
from daengs_backend.models.walk_storyboard import WalkStoryboard
from daengs_backend.schemas.walk_storyboard import StoryboardRequest
from daengs_backend.services.walk_diary.legacy.bundle import write_diary
from daengs_backend.services.walk_diary.lifecycle.generation import generate_diary, get_diary
from daengs_walk.diary.contracts.input import digest
from tests.walk.support.diary import place_payload, prose
from tests.walk.support.entry_v2 import AT, ENTRY, OWNER, WALK
from tests.walk.support.paths import REPO
from tests.walk.support.photo_input import context_envelope, entry


async def test_background_round_changes_keep_saved_board_across_connections(
    photo_database,
    monkeypatch,
):
    factory = photo_database
    monkeypatch.setattr(settings, "walk_diary_enabled", True)
    monkeypatch.setattr(settings, "walk_entry_context_enabled", True)
    original = entry().payload
    original["recorded_at"] = AT.isoformat()
    original["location"]["captured_at"] = AT.isoformat()
    raw = context_envelope()
    raw["target"].update(
        walk_id=str(WALK),
        id=str(ENTRY),
        event_at=AT.isoformat(),
        location=original["location"],
    )
    raw["provenance"]["retrieved_at"] = AT.isoformat()
    payload = place_payload(("place-a", "합성 카페", 60))
    raw.update(status="known", payload=payload, payload_sha256=digest(payload))
    job_id, envelope_id = uuid4(), uuid4()
    async with factory() as db:
        connection = (await (await db.connection()).get_raw_connection()).driver_connection
        await connection.execute(
            (REPO / "db/init/20_walk_storyboards.sql").read_text(encoding="utf-8")
        )
        db.add(
            WalkEntry(
                walk_id=WALK,
                id=ENTRY,
                revision=2,
                mutation_id=uuid4(),
                payload=original,
            )
        )
        await db.flush()
        db.add(
            Job(
                id=job_id,
                walk_id=WALK,
                entry_id=ENTRY,
                revision=2,
                policy_version="walk-entry-context-v1",
                tag="space.facility",
                state="completed",
                attempts=1,
                available_at=AT,
            )
        )
        await db.flush()
        db.add(
            Envelope(
                id=envelope_id,
                job_id=job_id,
                attempt=1,
                created_at=AT,
                envelope=raw,
            )
        )
        await db.commit()
    calls = 0

    async def writer(source, prepared):
        nonlocal calls
        calls += 1

        async def provider(payload, _schema):
            return prose(payload)

        return await write_diary(source, prepared, provider)

    request = StoryboardRequest(
        bundle_format="walk-diary-bundle-v1",
        expected_entries={ENTRY: 2},
        target_scene_count=3,
    )
    async with factory() as db:
        first = await generate_diary(db, OWNER, WALK, request, writer=writer)
    assert first.status == "ready" and first.bundle.model_status == "accepted"
    # Simulate a pre-upgrade stored board, then verify GET persists only the receipt.
    async with factory() as db:
        saved = await db.get(WalkStoryboard, WALK)
        saved.bundle = first.bundle.model_dump(mode="json")
        await db.commit()
    async with factory() as db:
        assert await get_diary(db, OWNER, WALK, 3) == first
    async with factory() as db:
        stored = deepcopy((await db.get(WalkStoryboard, WALK)).bundle)
        assert stored["format"] == "walk-diary-storage-v1"
        job = await db.get(Job, job_id)
        job.collection_round = 1
        # A later collection round changes the current background, never the original note.
        latest = deepcopy(raw)
        payload = place_payload(("place-b", "다른 합성 카페", 80))
        latest.update(id=str(uuid4()), payload=payload, payload_sha256=digest(payload))
        db.add(
            Envelope(
                id=uuid4(),
                job_id=job_id,
                attempt=1,
                collection_round=1,
                created_at=AT,
                envelope=latest,
            )
        )
        await db.commit()
    async with factory() as db:
        retained = await get_diary(db, OWNER, WALK, 3)
    assert retained == first.model_copy(update={"background_update_available": True})
    async with factory() as db:
        assert await generate_diary(db, OWNER, WALK, request, writer=writer) == retained
    assert calls == 1
    async with factory() as db:
        assert (await db.get(WalkStoryboard, WALK)).bundle == stored
        assert (await db.get(Envelope, envelope_id)).envelope == raw
        assert (await db.get(WalkEntry, (WALK, ENTRY))).payload == original
        assert len(list(await db.scalars(select(Envelope)))) == 2
        fresh = await generate_diary(
            db,
            OWNER,
            WALK,
            request.model_copy(update={"refresh": True}),
            writer=writer,
        )
    assert fresh.status == "ready" and fresh.generation == 2
    assert not fresh.background_update_available
    assert fresh.bundle.input_revision != first.bundle.input_revision and calls == 2
    async with factory() as db:
        row = await db.get(WalkEntry, (WALK, ENTRY))
        row.revision += 1
        row.payload = None
        await db.commit()
    async with factory() as db:
        deleted = await get_diary(db, OWNER, WALK, 3)
        assert deleted.status == "stale" and deleted.bundle is None
        await db.execute(text("DELETE FROM app_users WHERE id=:owner"), {"owner": OWNER})
        await db.commit()
        assert await db.scalar(text("SELECT count(*) FROM walk_storyboards")) == 0
