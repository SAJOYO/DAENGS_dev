"""Entry writes and durable context reservations share rollback and retry semantics."""

import uuid

import pytest
from sqlalchemy import func, select

from daengs_backend.config import settings
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.models.walk_entry_context import WalkEntryContextJob
from daengs_backend.models.walk_entry_v2 import WalkEntryMutation, WalkEntryPin
from daengs_backend.repositories import walk_entry_context as jobs
from daengs_backend.schemas.walk_entry import EntryWrite
from daengs_backend.schemas.walk_entry_v2 import EntryWriteV2
from daengs_backend.services import walk_entry, walk_entry_v2
from tests.walk.support.entry_v2 import AT, OWNER, WALK


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("failure", ["reservation", "commit"])
async def test_failed_write_rolls_back_jobs_and_same_request_can_retry(
    database, monkeypatch, version, failure
):
    monkeypatch.setattr(settings, "walk_entry_context_enabled", True)
    monkeypatch.setattr(settings, "walk_public_context_enabled", False)
    entry_id = uuid.uuid4()
    value = {
        "expected_revision": 0,
        "mutation_id": uuid.uuid4(),
        "content": {"kind": "note", "note": "산책 메모", "recorded_at": AT},
    }
    service, spec = (
        (walk_entry, EntryWrite.model_validate(value))
        if version == "v1"
        else (walk_entry_v2, EntryWriteV2.model_validate({**value, "pin": None}))
    )
    enqueue = jobs.enqueue

    async def fail_reservation(*args, **kwargs):
        await enqueue(*args, **kwargs)
        raise RuntimeError("reservation interrupted")

    async with database() as db:

        async def fail_commit():
            await db.flush()
            raise RuntimeError("commit interrupted")

        with monkeypatch.context() as patch:
            if failure == "reservation":
                patch.setattr(jobs, "enqueue", fail_reservation)
            else:
                patch.setattr(db, "commit", fail_commit)
            with pytest.raises(RuntimeError, match=f"{failure} interrupted"):
                await service.write(db, OWNER, WALK, entry_id, spec)
            await db.rollback()

    async with database() as db:
        for table in (WalkEntry, WalkEntryPin, WalkEntryMutation, WalkEntryContextJob):
            assert await db.scalar(select(func.count()).select_from(table)) == 0

    async with database() as db:
        saved = await service.write(db, OWNER, WALK, entry_id, spec)
    async with database() as db:
        pending = list(await db.scalars(select(WalkEntryContextJob)))
        assert {job.tag for job in pending} == set(jobs.TAGS)
        assert len(pending) == len(jobs.TAGS)
        assert all(job.state == "pending" and job.revision == 1 for job in pending)
        assert {job.policy_version for job in pending} == {
            jobs.POLICY if version == "v1" else jobs.PIN_POLICY
        }
        job_ids = {job.id for job in pending}
        for table in (WalkEntryPin, WalkEntryMutation):
            assert await db.scalar(select(func.count()).select_from(table)) == (version == "v2")
    async with database() as db:
        assert await service.write(db, OWNER, WALK, entry_id, spec) == saved
    async with database() as db:
        assert set(await db.scalars(select(WalkEntryContextJob.id))) == job_ids
