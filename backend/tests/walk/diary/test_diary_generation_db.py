"""Diary publication against the disposable walk PostgreSQL CI database."""

import asyncio

from sqlalchemy import text

from daengs_backend.config import settings
from daengs_backend.schemas.walk_storyboard import StoryboardRequest
from daengs_backend.services.walk_diary.lifecycle.generation import generate_diary, get_diary
from daengs_walk.diary_output import assemble_diary
from tests.walk.support.entry_v2 import OWNER, WALK
from tests.walk.support.paths import REPO as REPO_ROOT
from tests.walk.support.photo_database import publish, request


async def test_late_generation_cannot_replace_a_new_photo_snapshot(photo_database, monkeypatch):
    factory = photo_database
    monkeypatch.setattr(settings, "walk_diary_enabled", True)
    monkeypatch.setattr(settings, "walk_entry_context_enabled", False)
    root = REPO_ROOT
    async with factory() as db:
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        await raw.execute(
            (root / "db/migrations/2026-09-05_walk_storyboards.sql").read_text(encoding="utf-8")
        )
        await db.commit()
    spec = StoryboardRequest(
        bundle_format="walk-diary-bundle-v1", expected_entries={}, target_scene_count=3
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def call(body, *, wait=False):
        async with factory() as db:

            async def write(source, prepared):
                assert not db.in_transaction()
                if wait:
                    entered.set()
                    await release.wait()
                return assemble_diary(source, prepared.plan, None)

            return await generate_diary(db, OWNER, WALK, body, writer=write)

    old = asyncio.create_task(call(spec, wait=True))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # The photo write takes the same Walk row lock and must commit while the writer waits.
        await asyncio.wait_for(publish(factory, request()), 5)
        async with factory() as db:
            changed = await get_diary(db, OWNER, WALK, 3)
        assert changed.status == "stale" and changed.bundle is None
        current = spec.model_copy(update={"expected_photo_manifest": changed.photo_manifest})
        fresh = await asyncio.wait_for(call(current), 5)
        assert fresh.status == "ready" and fresh.generation == 2
        assert fresh.bundle.scenes[0].user_record.kind == "photo"
        release.set()
        delayed = await asyncio.wait_for(old, 5)
        assert delayed == fresh
        assert await call(current) == fresh
        async with factory() as db:
            assert (
                await db.scalar(text("SELECT bundle->>'format' FROM walk_storyboards"))
                == "walk-diary-storage-v1"
            )
            await db.execute(text("DELETE FROM app_users WHERE id=:id"), {"id": OWNER})
            await db.commit()
            assert await db.scalar(text("SELECT count(*) FROM walk_storyboards")) == 0
    finally:
        release.set()
        await asyncio.gather(old, return_exceptions=True)
