"""Photo manifest CAS/cascade against disposable PostgreSQL used by the walk CI job."""

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from daengs_backend.config import settings
from daengs_backend.schemas.walk_photo import PhotoManifestWrite
from daengs_backend.services import walk_photo as service
from tests.test_walk_entry_v2 import AT, OWNER, WALK
from tests.test_walk_entry_v2_db import database  # noqa: F401 -- shared disposable DB fixture

ROOT = Path(__file__).resolve().parents[2]
PUBLISHER, PHOTO = uuid.uuid4(), uuid.uuid4()


@pytest.fixture
async def photo_database(database, monkeypatch):  # noqa: F811 -- pytest injects imported fixture
    monkeypatch.setattr(settings, "walk_photo_metadata_enabled", True)
    async with database() as session:
        connection = await session.connection()
        raw = (await connection.get_raw_connection()).driver_connection
        for path in (
            "db/migrations/2026-09-09_walk_photo_manifests.sql",
            "db/migrations/2026-09-09_walk_photo_manifests.sql",
            "db/init/26_walk_photo_manifests.sql",
            "db/migrations/verify_2026-09-09_walk_photo_manifests.sql",
        ):
            await raw.execute((ROOT / path).read_text(encoding="utf-8"))
        await session.commit()
    return database


def request(revision=1, expected=0, *, empty=False, accuracy=5.0):
    return PhotoManifestWrite.model_validate(
        {
            "publisher_id": PUBLISHER,
            "revision": revision,
            "expected_revision": expected,
            "photos": []
            if empty
            else [
                {
                    "id": PHOTO,
                    "captured_at": AT,
                    "location_captured_at": AT,
                    "point": {"lat": 37.5, "lng": 127.0},
                    "accuracy_m": accuracy,
                }
            ],
        }
    )


async def publish(factory, spec):
    async with factory() as session:
        return await service.put(session, OWNER, WALK, spec)


async def test_metadata_retry_delete_and_ownership_use_real_storage(photo_database):
    factory = photo_database
    first = await publish(factory, request())
    assert await publish(factory, request()) == first
    removed = await publish(factory, request(2, 1, empty=True))
    assert removed.records[0].content is None and removed.records[0].revision == 2
    with pytest.raises(service.PhotoConflict):
        await publish(factory, request(3, 2))
    async with factory() as session:
        with pytest.raises(service.PhotoNotFound):
            await service.get(session, uuid.uuid4(), WALK)


async def test_competing_snapshots_have_exactly_one_cas_winner(photo_database):
    factory = photo_database
    await publish(factory, request())
    results = await asyncio.gather(
        publish(factory, request(2, 1, empty=True)),
        publish(factory, request(2, 1, accuracy=6.0)),
        return_exceptions=True,
    )
    assert sum(isinstance(r, service.PhotoConflict) for r in results) == 1
    assert sum(not isinstance(r, Exception) for r in results) == 1


async def test_account_deletion_cascades_photo_metadata(photo_database):
    factory = photo_database
    await publish(factory, request())
    async with factory() as session:
        await session.execute(text("DELETE FROM app_users WHERE id = :id"), {"id": OWNER})
        await session.commit()
        assert await session.scalar(text("SELECT count(*) FROM walk_photo_manifests")) == 0
