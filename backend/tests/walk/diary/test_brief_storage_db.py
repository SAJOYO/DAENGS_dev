"""Actual lifecycle reservation and JSONB round trip in disposable PostgreSQL."""

from dataclasses import replace

from sqlalchemy import text

from daengs_backend.config import settings
from daengs_backend.schemas.walk_generation import StoryboardRequest
from daengs_backend.services.walk_diary.lifecycle import relational
from daengs_backend.services.walk_diary.runtime import write_relational_board
from tests.walk.diary.test_brief_execution import prepare, public_collector  # noqa: F401
from tests.walk.diary.test_relational_http import brief_send
from tests.walk.support.entry_v2 import OWNER, WALK
from tests.walk.support.paths import REPO
from tests.walk.support.photo_database import publish, request


async def test_v8_lifecycle_jsonb_roundtrip(photo_database, prepare, monkeypatch):  # noqa: F811
    factory = photo_database
    monkeypatch.setattr(settings, "walk_diary_enabled", True)
    monkeypatch.setattr(settings, "walk_entry_context_enabled", False)
    async with factory() as db:
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        await raw.execute(
            (REPO / "db/migrations/2026-09-05_walk_storyboards.sql").read_text(encoding="utf-8")
        )
        await db.commit()
    await publish(factory, request())
    async with factory() as db:
        pending = await relational.get_relational(db, OWNER, WALK, 3)
    spec = StoryboardRequest(
        bundle_format="walk-relational-diary-v1",
        expected_entries={},
        expected_photo_manifest=pending.photo_manifest,
        target_scene_count=3,
    )
    async with factory() as db:

        async def writer(source, base, *, execution_policy):
            assert not db.in_transaction()
            return await write_relational_board(
                source,
                base,
                prepare=prepare,
                send=brief_send,
                execution_policy=replace(execution_policy, minimum_interval_s=0),
            )

        value = await relational.generate_relational(db, OWNER, WALK, spec, writer=writer)
        assert value.status == "ready", value
    async with factory() as db:
        saved = await db.scalar(
            text("SELECT bundle FROM walk_storyboards WHERE walk_id=:walk"), {"walk": WALK}
        )
        assert saved["payload"]["receipt"]["version"] == "relational-diary-skeleton-v8"
        assert await relational.get_relational(db, OWNER, WALK, 3) == value
    # A fresh photo revision invalidates the saved v8 through the real source read.
    await publish(factory, request(revision=2, expected=1, empty=True))
    async with factory() as db:
        changed = await relational.get_relational(db, OWNER, WALK, 3)
        assert changed.status == "stale" and changed.bundle is None
