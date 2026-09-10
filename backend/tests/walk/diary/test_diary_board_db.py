"""New JSONB receipts/leases use the existing table and owner lifecycle."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from daengs_backend.config import settings
from daengs_backend.models.walk_storyboard import WalkStoryboard
from daengs_backend.schemas.walk_storyboard import StoryboardRequest
from daengs_backend.services import walk_diary_writing as writing
from daengs_backend.services.walk_diary_generation import generate_diary, get_diary
from daengs_backend.services.walk_storyboard_state import StoryboardConflict
from daengs_walk.diary_board_output import BOARD_FORMAT
from daengs_walk.diary_output import assemble_diary
from tests.walk.support.entry_v2 import OWNER, WALK
from tests.walk.support.paths import REPO


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
    assert stored["format"] == "walk-diary-board-storage-v1"
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
            return assemble_diary(source, prepared.plan, None)

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
        return assemble_diary(source, prepared.plan, None)

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
