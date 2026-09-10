"""Runtime checks reuse production SQL, on an isolated localhost schema only."""

import pytest
from sqlalchemy import text

from daengs_backend.cli import walk_runtime_check as runtime
from daengs_backend.config import settings
from tests.walk.context.test_walk_entry_context_db import database as context_database  # noqa: F401
from tests.walk.support.paths import REPO


@pytest.fixture
async def runtime_database(context_database, monkeypatch):  # noqa: F811 - imported pytest fixture
    engine = context_database.kw["bind"]
    async with engine.begin() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        for name in (
            "20_walk_storyboards.sql",
            "25_walk_entry_pins.sql",
            "26_walk_photo_manifests.sql",
            "28_walk_commerce_context.sql",
        ):
            await raw.execute((REPO / "db/init" / name).read_text(encoding="utf-8"))
    monkeypatch.setattr(runtime, "create_async_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", True)
    monkeypatch.setattr(settings, "walk_entry_v2_write_enabled", True)
    monkeypatch.setattr(settings, "walk_photo_metadata_enabled", True)
    return engine


async def test_schema_verifiers_pass_then_reject_missing_sixth_tag(runtime_database):
    result = await runtime.verify_schema(REPO / "db/migrations", "unused")
    assert "2026-09-09_walk_photo_manifests" in result
    async with runtime_database.begin() as conn:
        await conn.execute(
            text(
                "ALTER TABLE walk_entry_context_jobs DROP CONSTRAINT walk_entry_context_jobs_tag_check"
            )
        )
    with pytest.raises(Exception, match="tag constraint mismatch"):
        await runtime.verify_schema(REPO / "db/migrations", "unused")


async def test_verifier_cannot_write_even_if_a_future_script_changes(runtime_database, tmp_path):
    for source in (REPO / "db/migrations").glob("verify_*walk*.sql"):
        (tmp_path / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    target = tmp_path / "verify_2026-09-05_walk_entries.sql"
    target.write_text(
        target.read_text(encoding="utf-8") + "\nCREATE TABLE forbidden_write (id int);",
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="read-only transaction"):
        await runtime.verify_schema(tmp_path, "unused")
    async with runtime_database.connect() as conn:
        assert await conn.scalar(text("SELECT to_regclass('forbidden_write')")) is None
