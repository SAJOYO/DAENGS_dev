"""Photo manifest CAS/cascade against disposable PostgreSQL used by the walk CI job."""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from daengs_backend.services.walk_photos import api as service
from tests.walk.support.entry_v2 import OWNER, WALK
from tests.walk.support.photo_database import publish, request


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
