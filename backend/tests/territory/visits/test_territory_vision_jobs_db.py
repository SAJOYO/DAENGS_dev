"""Real PostgreSQL leases, recovery, races and atomic verdicts; providers are local fakes."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, text

from daengs_backend.config import settings
from daengs_backend.core import database as connections
from daengs_backend.models.territory import TerritoryAttempt, VerifiedVisit
from daengs_backend.services import territory
from daengs_backend.services import territory_vision as vision
from daengs_backend.services import territory_vision_jobs as jobs
from tests.territory.support.ownership import begin, mark, photo
from tests.territory.support.paths import REPO


class Storage:
    cleanup_fails = False

    def stat(self, key):
        from daengs_backend.core.storage import StoredObject

        return StoredObject("generation-1", 4, "image/jpeg")

    def read_bytes(self, key, *, generation, max_bytes):
        assert generation == "generation-1" and max_bytes == 4
        return b"jpeg"

    def redact(self, key, *, generation):
        assert generation == "generation-1"
        if self.cleanup_fails:
            raise OSError("storage unavailable")


class Classifier:
    provider_name = "test-vlm"
    model_version = "test-v1"

    def __init__(self):
        self.calls = 0
        self.failure = None

    async def classify(self, **kwargs):
        self.calls += 1
        if self.failure:
            raise self.failure
        return vision.TerritoryVisionResult("verified", "dog_visible")


@pytest.fixture
async def pending(database, actors, monkeypatch):
    monkeypatch.setattr(settings, "activity_game_enabled", False)
    monkeypatch.setattr(connections, "worker_session", database)
    storage = Storage()
    monkeypatch.setattr(vision, "get_storage", lambda: storage)
    monkeypatch.setattr(territory, "get_storage", lambda: storage)
    attempt_id = uuid.uuid4()
    async with database() as db:
        db.add(
            TerritoryAttempt(
                id=attempt_id,
                app_user_id=actors[0][0],
                client_capture_id=uuid.uuid4(),
                client_session_id=uuid.uuid4(),
                site_id="site-1",
                captured_at=datetime.now(UTC),
                capture_lat=37.5,
                capture_lng=127,
                site_lat=37.5,
                site_lng=127,
                accuracy_m=1,
                distance_m=0,
                is_mock=False,
                status="VISION_PENDING",
                photo_storage_key=f"territory/{attempt_id}",
                photo_content_type="image/jpeg",
                photo_object_generation="generation-1",
                photo_size_bytes=4,
            )
        )
        await db.commit()
    return attempt_id, storage


async def row(database, attempt_id):
    async with database() as db:
        return await db.get(TerritoryAttempt, attempt_id)


async def make_due(database, attempt_id, *, expire=False):
    async with database() as db:
        saved = await db.get(TerritoryAttempt, attempt_id)
        past = datetime.now(UTC) - timedelta(seconds=1)
        saved.vision_available_at = saved.vision_dispatch_after = past
        if expire:
            saved.vision_lease_until = past
        await db.commit()


async def test_confirm_publish_failure_recovers_without_app_retry(
    database, actors, pending, monkeypatch
):
    attempt_id, _ = pending
    async with database() as db:
        saved = await db.get(TerritoryAttempt, attempt_id)
        saved.status = "PENDING_UPLOAD"
        saved.photo_object_generation = saved.photo_size_bytes = None
        await db.commit()
    from daengs_backend.tasks.territory import verify_photo

    def unavailable(*args):
        raise OSError("private broker address")

    monkeypatch.setattr(verify_photo, "delay", unavailable)
    async with database() as db:
        with pytest.raises(territory.TerritoryVisionQueueUnavailable):
            await territory.confirm_upload(db, actors[0][0], attempt_id)
    assert (await row(database, attempt_id)).status == "VISION_PENDING"
    published = []
    result = await jobs.recover_pending(factory=database, publish=published.append)
    assert result == {"selected": 1, "published": 1, "failed": 0}
    assert published == [attempt_id]
    classifier = Classifier()
    await vision.process_attempt(attempt_id, classifier=classifier)
    assert (await row(database, attempt_id)).status == "VERIFIED"
    assert classifier.calls == 1


async def test_duplicate_delivery_calls_model_once_without_db_session_during_io(
    database, pending, monkeypatch
):
    attempt_id, _ = pending
    entered, release = asyncio.Event(), asyncio.Event()
    sessions = []

    @asynccontextmanager
    async def factory():
        async with database() as db:
            sessions.append(db)
            try:
                yield db
            finally:
                sessions.remove(db)

    monkeypatch.setattr(connections, "worker_session", factory)
    classifier = Classifier()

    async def classify(**kwargs):
        assert sessions == []
        classifier.calls += 1
        entered.set()
        await release.wait()
        return vision.TerritoryVisionResult("verified", "dog_visible")

    classifier.classify = classify
    first = asyncio.create_task(vision.process_attempt(attempt_id, classifier=classifier))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert await vision.process_attempt(attempt_id, classifier=classifier) is None
        assert classifier.calls == 1
        assert (await jobs.recover_pending(factory=database, publish=lambda _: None))[
            "selected"
        ] == 0
    finally:
        release.set()
        await first
    assert (await row(database, attempt_id)).vision_attempts == 1


@pytest.mark.parametrize("late_decision", ["verified", "failed"])
async def test_expired_worker_cannot_finish_or_release_replacement_lease(
    database, pending, late_decision
):
    attempt_id, _ = pending
    async with database() as db:
        old = await jobs.claim(db, attempt_id)
    await make_due(database, attempt_id, expire=True)
    async with database() as db:
        current = await jobs.claim(db, attempt_id)
    assert old.token != current.token
    async with database() as db:
        assert not await jobs.retry(db, attempt_id, old, reason="late failure")
    assert not await vision._complete(
        attempt_id, old, Classifier(), decision=late_decision, reason="late"
    )
    saved = await row(database, attempt_id)
    assert saved.status == "VISION_PENDING" and saved.vision_lease_token == current.token
    assert await vision._complete(
        attempt_id, current, Classifier(), decision="rejected", reason="dog_not_visible"
    )
    assert not await vision._complete(
        attempt_id, old, Classifier(), decision="verified", reason="late"
    )
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(VerifiedVisit)) == 0


async def test_generation_change_fences_result(database, pending):
    attempt_id, _ = pending
    async with database() as db:
        lease = await jobs.claim(db, attempt_id)
    async with database() as db:
        saved = await db.get(TerritoryAttempt, attempt_id)
        saved.photo_object_generation = "different-generation"
        await db.commit()
    assert not await vision._complete(
        attempt_id, lease, Classifier(), decision="verified", reason="dog_visible"
    )
    assert (await row(database, attempt_id)).status == "VISION_PENDING"


async def test_unfenced_result_cannot_bypass_active_lease(database, pending):
    attempt_id, _ = pending
    async with database() as db:
        await jobs.claim(db, attempt_id)
    async with database() as db:
        with pytest.raises(territory.TerritoryAttemptConflictError) as error:
            await territory.record_vision_decision(
                db, attempt_id, decision="failed", model="legacy", model_version="v1"
            )
        assert error.value.code == "vision_lease_lost"
    assert (await row(database, attempt_id)).status == "VISION_PENDING"


async def test_preloaded_session_rechecks_replaced_lease_under_lock(database, pending):
    attempt_id, _ = pending
    async with database() as db:
        old = await jobs.claim(db, attempt_id)
    async with database() as stale:
        cached = await stale.get(TerritoryAttempt, attempt_id)
        await make_due(database, attempt_id, expire=True)
        async with database() as db:
            current = await jobs.claim(db, attempt_id)
        assert cached.vision_lease_token == old.token
        with pytest.raises(territory.TerritoryAttemptConflictError) as error:
            await territory.record_vision_decision(
                stale,
                attempt_id,
                decision="verified",
                model="old",
                model_version="v1",
                lease_token=old.token,
                generation=old.generation,
            )
        assert error.value.code == "vision_lease_lost"
    assert (await row(database, attempt_id)).vision_lease_token == current.token


async def test_cancelled_worker_is_recovered_after_lease_expiry(database, pending):
    attempt_id, _ = pending
    entered = asyncio.Event()

    async def blocked(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    classifier = Classifier()
    classifier.classify = blocked
    worker = asyncio.create_task(vision.process_attempt(attempt_id, classifier=classifier))
    try:
        await asyncio.wait_for(entered.wait(), 3)
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
    assert (await row(database, attempt_id)).vision_lease_token is not None
    await make_due(database, attempt_id, expire=True)
    published = []
    assert (await jobs.recover_pending(factory=database, publish=published.append))[
        "published"
    ] == 1
    assert published == [attempt_id]
    await vision.process_attempt(attempt_id, classifier=Classifier())
    assert (await row(database, attempt_id)).status == "VERIFIED"


async def test_transient_retry_budget_survives_new_deliveries(database, pending):
    attempt_id, _ = pending
    classifier = Classifier()
    classifier.failure = vision.TerritoryVisionTransientError("vision_provider_unavailable")
    with pytest.raises(vision.TerritoryVisionTransientError):
        await vision.process_attempt(attempt_id, classifier=classifier)
    saved = await row(database, attempt_id)
    assert saved.vision_lease_token is None and saved.vision_attempts == 1
    assert await vision.process_attempt(attempt_id, classifier=classifier) is None
    assert classifier.calls == 1
    await make_due(database, attempt_id)
    await vision.process_attempt(attempt_id, classifier=classifier)
    await vision.process_attempt(attempt_id, classifier=classifier)
    saved = await row(database, attempt_id)
    assert saved.status == "FAILED" and saved.vision_attempts == 2
    assert saved.decision_reason == "vision_provider_unavailable"
    assert classifier.calls == 2


async def test_timeout_keeps_lease_until_expiry_before_another_model_call(
    database, pending, monkeypatch
):
    attempt_id, _ = pending
    monkeypatch.setattr(settings, "territory_vision_timeout_ms", 1)
    classifier = Classifier()

    async def blocked(**kwargs):
        classifier.calls += 1
        await asyncio.Event().wait()

    classifier.classify = blocked
    with pytest.raises(vision.TerritoryVisionTransientError, match="vision_timeout"):
        await vision.process_attempt(attempt_id, classifier=classifier)
    saved = await row(database, attempt_id)
    assert saved.vision_lease_token is not None
    assert saved.vision_available_at == saved.vision_lease_until == saved.vision_dispatch_after
    assert await vision.process_attempt(attempt_id, classifier=classifier) is None
    assert classifier.calls == 1
    assert (await jobs.recover_pending(factory=database, publish=lambda _: None))["selected"] == 0
    await make_due(database, attempt_id, expire=True)
    await vision.process_attempt(attempt_id, classifier=Classifier())
    assert (await row(database, attempt_id)).status == "VERIFIED"


async def test_crashed_last_attempt_is_failed_without_another_model_call(database, pending):
    attempt_id, _ = pending
    for _ in range(2):
        async with database() as db:
            assert await jobs.claim(db, attempt_id) is not None
        await make_due(database, attempt_id, expire=True)
    classifier = Classifier()
    await vision.process_attempt(attempt_id, classifier=classifier)
    saved = await row(database, attempt_id)
    assert saved.status == "FAILED" and saved.decision_reason == "vision_attempts_exhausted"
    assert classifier.calls == 0


async def test_terminal_cleanup_recovers_without_reclassifying(database, pending):
    attempt_id, storage = pending
    classifier = Classifier()
    storage.cleanup_fails = True
    with pytest.raises(vision.TerritoryVisionTransientError):
        await vision.process_attempt(attempt_id, classifier=classifier)
    saved = await row(database, attempt_id)
    assert saved.status == "VERIFIED" and saved.photo_redacted_at is None
    published = []
    assert (await jobs.recover_pending(factory=database, publish=published.append))["selected"] == 1
    storage.cleanup_fails = False
    assert await vision.process_attempt(attempt_id, classifier=classifier) is None
    assert (await row(database, attempt_id)).photo_redacted_at is not None
    assert classifier.calls == 1


async def test_recovery_reservation_is_bounded_and_survives_publish_failure(database, pending):
    attempt_id, _ = pending

    def unavailable(_):
        raise territory.TerritoryVisionQueueUnavailable("unavailable")

    assert await jobs.recover_pending(factory=database, publish=unavailable) == {
        "selected": 1,
        "published": 0,
        "failed": 1,
    }
    assert (await jobs.recover_pending(factory=database, publish=unavailable))["selected"] == 0
    await make_due(database, attempt_id)
    published = []
    results = await asyncio.gather(
        *(jobs.recover_pending(factory=database, publish=published.append) for _ in range(2))
    )
    assert sum(result["selected"] for result in results) == 1
    assert published == [attempt_id]


async def test_recovery_batches_progress_without_publishing_under_a_session(
    database, pending, monkeypatch
):
    attempt_id, _ = pending
    async with database() as db:
        original = await db.get(TerritoryAttempt, attempt_id)
        values = {
            column.name: getattr(original, column.name)
            for column in TerritoryAttempt.__table__.columns
        }
        for _ in range(4):
            new_id = uuid.uuid4()
            db.add(
                TerritoryAttempt(
                    **{
                        **values,
                        "id": new_id,
                        "client_capture_id": uuid.uuid4(),
                        "photo_storage_key": f"territory/{new_id}",
                    }
                )
            )
        await db.commit()
    monkeypatch.setattr(jobs, "DISPATCH_LIMIT", 2)
    sessions, published = [], []

    @asynccontextmanager
    async def factory():
        async with database() as db:
            sessions.append(db)
            try:
                yield db
            finally:
                sessions.remove(db)

    def publish(value):
        assert sessions == []
        published.append(value)

    counts = [
        (await jobs.recover_pending(factory=factory, publish=publish))["selected"] for _ in range(3)
    ]
    assert counts == [2, 2, 1]
    assert len(published) == len(set(published)) == 5


async def test_lease_commit_failure_does_not_consume_attempt(database, pending, monkeypatch):
    attempt_id, _ = pending
    async with database() as db:
        monkeypatch.setattr(db, "commit", AsyncMock(side_effect=RuntimeError("commit failed")))
        with pytest.raises(RuntimeError, match="commit failed"):
            await jobs.claim(db, attempt_id)
        await db.rollback()
    saved = await row(database, attempt_id)
    assert saved.vision_attempts == 0 and saved.vision_lease_token is None


@pytest.mark.parametrize("state", ["legacy_pending", "legacy_terminal", "live_lease"])
async def test_migration_upgrades_old_rows_and_reapplication_preserves_live_lease(
    database, pending, state
):
    attempt_id, _ = pending
    lease = None
    async with database() as db:
        if state == "live_lease":
            lease = await jobs.claim(db, attempt_id)
        elif state == "legacy_terminal":
            saved = await db.get(TerritoryAttempt, attempt_id)
            saved.status, saved.vision_model, saved.vision_model_version = "REJECTED", "old", "v1"
            await db.commit()
        if state != "live_lease":
            # The fixture owns this unique schema. Recreate the pre-migration shape with real rows.
            await db.execute(
                text(
                    "ALTER TABLE territory_attempts DROP COLUMN vision_lease_token CASCADE, "
                    "DROP COLUMN vision_lease_until CASCADE, DROP COLUMN vision_attempts CASCADE, "
                    "DROP COLUMN vision_available_at CASCADE, DROP COLUMN vision_dispatch_after CASCADE, "
                    "DROP COLUMN vision_retry_reason CASCADE"
                )
            )
            await db.commit()
        connection = await db.connection()
        raw = (await connection.get_raw_connection()).driver_connection
        migration = (REPO / "db/migrations/2026-09-12_territory_vision_jobs.sql").read_text("utf-8")
        # The fixture supplies the transaction; preserve the production SQL statements themselves.
        migration = migration.replace("BEGIN;", "").replace("COMMIT;", "")
        await raw.execute(migration)
        await raw.execute(migration)
        await raw.execute(
            (REPO / "db/migrations/verify_2026-09-12_territory_vision_jobs.sql").read_text("utf-8")
        )
        await db.commit()
    saved = await row(database, attempt_id)
    assert saved.photo_object_generation == "generation-1"
    assert saved.status == ("REJECTED" if state == "legacy_terminal" else "VISION_PENDING")
    assert saved.vision_attempts == (1 if lease else 0)
    assert saved.vision_lease_token == (lease.token if lease else None)
    assert saved.vision_dispatch_after <= datetime.now(UTC)


async def test_deleted_owner_during_model_call_leaves_no_verdict(database, actors, pending):
    attempt_id, _ = pending
    classifier = Classifier()

    async def classify(**kwargs):
        async with database() as db:
            await db.execute(text("DELETE FROM app_users WHERE id=:id"), {"id": actors[0][0]})
            await db.commit()
        return vision.TerritoryVisionResult("verified", "dog_visible")

    classifier.classify = classify
    assert await vision.process_attempt(attempt_id, classifier=classifier) is None
    assert await row(database, attempt_id) is None


async def test_verdict_and_bound_claim_roll_back_together(database, actors, pending, monkeypatch):
    owner, pet = actors[0][0], actors[1][0]
    client = await begin(database, owner, [pet])
    claim = await mark(database, owner, client, pet)
    attempt_id = await photo(database, owner, client, claim)
    async with database() as db:
        saved = await db.get(TerritoryAttempt, attempt_id)
        saved.photo_object_generation = "generation-1"
        await db.commit()
        occupancy_before = await db.scalar(
            text("SELECT row_to_json(t) FROM territory_occupancies t")
        )
    from daengs_backend.services import territory_ownership

    original = territory_ownership.apply_photo_decision

    async def failed_application(*args):
        await original(*args)
        raise RuntimeError("transaction interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(territory_ownership, "apply_photo_decision", failed_application)
        with pytest.raises(vision.TerritoryVisionTransientError):
            await vision.process_attempt(attempt_id, classifier=Classifier())
    saved = await row(database, attempt_id)
    assert saved.status == "VISION_PENDING" and saved.vision_lease_token is not None
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(VerifiedVisit)) == 0
        assert (
            await db.scalar(text("SELECT row_to_json(t) FROM territory_occupancies t"))
            == occupancy_before
        )
    await make_due(database, attempt_id, expire=True)
    await vision.process_attempt(attempt_id, classifier=Classifier())
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(VerifiedVisit)) == 1
        assert await db.scalar(text("SELECT count(*) FROM territory_occupancies")) == 1
        assert (
            await db.scalar(text("SELECT row_to_json(t) FROM territory_occupancies t"))
            != occupancy_before
        )
