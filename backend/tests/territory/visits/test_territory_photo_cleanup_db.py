"""Permanent cleanup conflicts stop; transient failures, resumption and races stay safe."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from threading import Event

import pytest
from sqlalchemy import delete

from daengs_backend.core import database as connections
from daengs_backend.core.storage import LocalBridgeStorage, StorageObjectChangedError
from daengs_backend.models.territory import PHOTO_CLEANUP_BLOCKED_REASON, TerritoryAttempt
from daengs_backend.services import territory
from daengs_backend.services import territory_vision as vision
from daengs_backend.services import territory_vision_jobs as jobs
from tests.territory.visits import test_territory_vision_jobs_db as job_tests
from tests.territory.visits.test_territory_vision_inventory_db import probe_module
from tests.territory.visits.test_territory_vision_jobs_db import (
    Classifier,
    make_due,
    row,
)

pending = job_tests.pending


@pytest.fixture
async def terminal(database, pending):
    attempt_id, storage = pending
    async with database() as db:
        attempt = await db.get(TerritoryAttempt, attempt_id)
        attempt.status = "FAILED"
        attempt.vision_model = attempt.vision_model_version = "test"
        attempt.decision_reason = "test-failure"
        await db.commit()
    return attempt_id, storage


async def resume(database, attempt_id, generation="generation-1"):
    async with database() as db:
        result = await territory.resume_photo_cleanup(
            db, attempt_id, expected_generation=generation
        )
        assert not db.in_transaction()
        return result


@pytest.mark.parametrize("damage", ["missing", "different_generation"])
async def test_real_file_conflict_stops_dispatch_and_queued_duplicates_then_resumes(
    database, pending, monkeypatch, tmp_path, damage
):
    attempt_id, _ = pending
    storage = LocalBridgeStorage(root=tmp_path, base_url="http://test.invalid")
    key = f"territory/{attempt_id}"
    storage.write_if_absent(key, b"jpeg")
    generation = storage.stat(key).generation
    async with database() as db:
        attempt = await db.get(TerritoryAttempt, attempt_id)
        attempt.photo_object_generation = generation
        await db.commit()
    monkeypatch.setattr(vision, "get_storage", lambda: storage)
    monkeypatch.setattr(territory, "get_storage", lambda: storage)
    if damage == "missing":
        storage.delete(key, generation=generation)
    else:
        storage.write(key, b"changed bytes")
    classifier = Classifier()
    with pytest.raises(vision.TerritoryVisionPermanentError, match="photo_cleanup_conflict"):
        await vision.process_attempt(attempt_id, classifier=classifier)
    saved = await row(database, attempt_id)
    assert saved.status == "FAILED" and saved.vision_retry_reason == PHOTO_CLEANUP_BLOCKED_REASON
    decision_reason = saved.decision_reason
    real_redact = storage.redact
    calls = []

    def observed_redact(*args, **kwargs):
        calls.append(args)
        return real_redact(*args, **kwargs)

    monkeypatch.setattr(storage, "redact", observed_redact)
    published = []
    for _ in range(3):
        await make_due(database, attempt_id)
        assert (await jobs.recover_pending(factory=database, publish=published.append))[
            "selected"
        ] == 0
        assert await vision.process_attempt(attempt_id, classifier=classifier) is None
    assert calls == published == []
    if damage == "missing":
        assert storage.stat(key) is None
    else:
        assert storage.local_path(key).read_bytes() == b"changed bytes"
    snapshot = await probe_module().database_inventory(engine=database.kw["bind"])
    assert snapshot["backlog"]["cleanup_blocked_count"] == 1
    assert snapshot["backlog"]["cleanup_dispatch_due_count"] == 0
    assert not await resume(database, attempt_id, "wrong-generation")
    # An operator restores the exact original bytes in this disposable storage.
    storage.write(key, b"jpeg")
    assert await resume(database, attempt_id, generation)
    assert not await resume(database, attempt_id, generation)
    await make_due(database, attempt_id)
    assert (await jobs.recover_pending(factory=database, publish=published.append))[
        "published"
    ] == 1
    assert await vision.process_attempt(attempt_id, classifier=classifier) is None
    saved = await row(database, attempt_id)
    assert saved.photo_redacted_at is not None and saved.vision_retry_reason is None
    assert saved.status == "FAILED" and saved.decision_reason == decision_reason
    assert saved.vision_attempts == 1 and classifier.calls == 0
    assert storage.local_path(key).read_bytes() == b""
    with pytest.raises(FileExistsError):
        storage.write_if_absent(key, b"stale-upload")
    assert not await resume(database, attempt_id, generation)


async def test_transient_cleanup_failure_remains_automatically_retryable(database, terminal):
    attempt_id, storage = terminal
    storage.cleanup_fails = True
    with pytest.raises(vision.TerritoryVisionTransientError):
        await vision.process_attempt(attempt_id)
    assert (await row(database, attempt_id)).vision_retry_reason is None
    published = []
    assert (await jobs.recover_pending(factory=database, publish=published.append))[
        "published"
    ] == 1
    storage.cleanup_fails = False
    await vision.process_attempt(attempt_id)
    assert (await row(database, attempt_id)).photo_redacted_at is not None


async def test_resuming_unrepaired_object_blocks_again_without_model_call(
    database, terminal, monkeypatch
):
    attempt_id, storage = terminal

    def conflict(*args, **kwargs):
        raise StorageObjectChangedError("unrepaired object")

    monkeypatch.setattr(storage, "redact", conflict)
    classifier = Classifier()
    with pytest.raises(vision.TerritoryVisionPermanentError):
        await vision.process_attempt(attempt_id, classifier=classifier)
    before = await row(database, attempt_id)
    assert await resume(database, attempt_id)
    with pytest.raises(vision.TerritoryVisionPermanentError):
        await vision.process_attempt(attempt_id, classifier=classifier)
    after = await row(database, attempt_id)
    assert after.vision_retry_reason == PHOTO_CLEANUP_BLOCKED_REASON
    assert after.photo_redacted_at is None
    assert after.status == before.status and after.decision_reason == before.decision_reason
    assert after.vision_attempts == before.vision_attempts and classifier.calls == 0


async def test_preloaded_decision_cannot_bypass_a_new_cleanup_block(database, terminal):
    attempt_id, storage = terminal
    async with database() as stale:
        cached = await stale.get(TerritoryAttempt, attempt_id)
        async with database() as changing:
            current = await changing.get(TerritoryAttempt, attempt_id)
            current.vision_retry_reason = PHOTO_CLEANUP_BLOCKED_REASON
            await changing.commit()
        assert cached.vision_retry_reason is None
        storage.cleanup_fails = True  # Any attempted I/O would fail this test.
        result = await territory.record_vision_decision(
            stale, attempt_id, decision="failed", model="test", model_version="test"
        )
        assert result.vision_retry_reason == PHOTO_CLEANUP_BLOCKED_REASON
        assert result.photo_redacted_at is None and not stale.in_transaction()


@pytest.mark.parametrize("winner", ["completed", "resumed", "identity_changed", "deleted"])
async def test_late_cleanup_failure_cannot_replace_newer_state(
    database, terminal, monkeypatch, winner
):
    attempt_id, storage = terminal
    entered, release = Event(), Event()
    calls = []

    def cleanup(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test release timed out")
            raise StorageObjectChangedError("late-conflict")
        if winner == "resumed":
            raise StorageObjectChangedError("current-conflict")

    monkeypatch.setattr(storage, "redact", cleanup)
    late = asyncio.create_task(vision.process_attempt(attempt_id))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        if winner == "completed":
            await vision.process_attempt(attempt_id)
        elif winner == "resumed":
            with pytest.raises(vision.TerritoryVisionPermanentError):
                await vision.process_attempt(attempt_id)
            assert await resume(database, attempt_id)
        else:
            async with database() as db:
                if winner == "deleted":
                    await db.execute(
                        delete(TerritoryAttempt).where(TerritoryAttempt.id == attempt_id)
                    )
                else:
                    current = await db.get(TerritoryAttempt, attempt_id)
                    current.photo_object_generation = "generation-2"
                await db.commit()
        before = await row(database, attempt_id)
        release.set()
        with pytest.raises(vision.TerritoryVisionPermanentError, match="photo_cleanup_conflict"):
            await late
        after = await row(database, attempt_id)
        if winner == "deleted":
            assert after is None
        else:
            assert after.vision_retry_reason is None
            assert after.photo_redacted_at == before.photo_redacted_at
            assert after.photo_object_generation == before.photo_object_generation
            assert after.vision_dispatch_after == before.vision_dispatch_after
    finally:
        release.set()
        await asyncio.gather(late, return_exceptions=True)


async def test_successful_cleanup_clears_an_overlapping_failure(database, terminal, monkeypatch):
    attempt_id, storage = terminal
    entered, release = Event(), Event()
    calls = []

    def cleanup(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test release timed out")
        else:
            raise StorageObjectChangedError("conflict before successful cleanup")

    monkeypatch.setattr(storage, "redact", cleanup)
    successful = asyncio.create_task(vision.process_attempt(attempt_id))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        with pytest.raises(vision.TerritoryVisionPermanentError):
            await vision.process_attempt(attempt_id)
        assert (await row(database, attempt_id)).vision_retry_reason == PHOTO_CLEANUP_BLOCKED_REASON
        release.set()
        await successful
        saved = await row(database, attempt_id)
        assert saved.photo_redacted_at is not None and saved.vision_retry_reason is None
    finally:
        release.set()
        await asyncio.gather(successful, return_exceptions=True)


async def test_recovery_during_slow_cleanup_cannot_suppress_a_permanent_conflict(
    database, terminal, monkeypatch
):
    attempt_id, storage = terminal
    entered, release = Event(), Event()

    def cleanup(*args, **kwargs):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test release timed out")
        raise StorageObjectChangedError("slow conflict")

    monkeypatch.setattr(storage, "redact", cleanup)
    slow = asyncio.create_task(vision.process_attempt(attempt_id))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        published = []
        result = await jobs.recover_pending(factory=database, publish=published.append)
        assert result["published"] == 1 and published == [attempt_id]
        release.set()
        with pytest.raises(vision.TerritoryVisionPermanentError):
            await slow
        saved = await row(database, attempt_id)
        assert saved.vision_retry_reason == PHOTO_CLEANUP_BLOCKED_REASON
        assert saved.photo_redacted_at is None
        # The extra message reserved during I/O acknowledges without trying storage again.
        assert await vision.process_attempt(attempt_id) is None
    finally:
        release.set()
        await asyncio.gather(slow, return_exceptions=True)


async def test_block_commit_failure_remains_discoverable(database, terminal, monkeypatch):
    attempt_id, storage = terminal

    def conflict(*args, **kwargs):
        raise StorageObjectChangedError("conflict")

    monkeypatch.setattr(storage, "redact", conflict)

    @asynccontextmanager
    async def fails_to_persist_block():
        async with database() as db:
            commit = db.commit

            async def fail():
                if any(
                    getattr(obj, "vision_retry_reason", None) == PHOTO_CLEANUP_BLOCKED_REASON
                    for obj in db.dirty
                ):
                    raise RuntimeError("injected commit failure")
                await commit()

            monkeypatch.setattr(db, "commit", fail)
            yield db

    monkeypatch.setattr(connections, "worker_session", fails_to_persist_block)
    with pytest.raises(vision.TerritoryVisionTransientError):
        await vision.process_attempt(attempt_id)
    assert (await row(database, attempt_id)).vision_retry_reason is None
    assert (await jobs.recover_pending(factory=database, publish=lambda _: None))["published"] == 1
    monkeypatch.setattr(connections, "worker_session", database)
    with pytest.raises(vision.TerritoryVisionPermanentError):
        await vision.process_attempt(attempt_id)
    assert (await row(database, attempt_id)).vision_retry_reason == PHOTO_CLEANUP_BLOCKED_REASON


async def test_resume_never_reopens_pending_missing_or_unblocked_attempts(database, pending):
    attempt_id, _ = pending
    assert not await resume(database, attempt_id)
    assert not await resume(database, uuid.uuid4())
    assert (await row(database, attempt_id)).status == "VISION_PENDING"
