"""Shared photo database test builders; no test cases."""

import uuid

import pytest

from daengs_backend.config import settings
from daengs_backend.schemas.walk_photo import PhotoManifestWrite
from daengs_backend.services import walk_photo as service
from tests.walk.support.entry_v2 import AT, OWNER, WALK
from tests.walk.support.paths import REPO as REPO_ROOT

ROOT = REPO_ROOT

PUBLISHER, PHOTO = uuid.uuid4(), uuid.uuid4()


@pytest.fixture
async def photo_database(database, monkeypatch):
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
