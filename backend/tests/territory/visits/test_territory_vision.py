"""점령지 사진 판정 provider·worker 경계 회귀."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from daengs_backend.core.storage import StorageObjectChangedError
from daengs_backend.services import territory_vision
from daengs_backend.services.territory_vision import (
    GeminiTerritoryVision,
    TerritoryVisionResult,
    TerritoryVisionTransientError,
)
from daengs_backend.services.territory_vision_jobs import VisionLease
from daengs_backend.tasks import territory as territory_task


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"verdict": "dog_visible"}, TerritoryVisionResult("verified", "dog_visible")),
        (
            {"verdict": "no_dog_visible"},
            TerritoryVisionResult("rejected", "dog_not_visible"),
        ),
        (
            {"verdict": "uncertain"},
            TerritoryVisionResult("rejected", "dog_presence_uncertain"),
        ),
    ],
)
async def test_provider_output_maps_to_stable_domain_decisions(raw, expected):
    async def generate(photo: bytes, content_type: str):
        assert photo == b"jpeg"
        assert content_type == "image/jpeg"
        return raw

    result = await GeminiTerritoryVision(generate).classify(
        photo=b"jpeg", content_type="image/jpeg"
    )
    assert result == expected


async def test_invalid_provider_output_is_retryable_without_leaking_raw_output():
    async def generate(photo: bytes, content_type: str):
        return {"verdict": "please trust me", "private": "provider detail"}

    with pytest.raises(TerritoryVisionTransientError) as caught:
        await GeminiTerritoryVision(generate).classify(photo=b"jpeg", content_type="image/jpeg")
    assert caught.value.reason_code == "vision_invalid_response"
    assert "private" not in str(caught.value)


async def test_worker_reads_only_frozen_generation_and_records_result(monkeypatch):
    attempt_id = uuid.uuid4()
    evidence = VisionLease(
        token=uuid.uuid4(),
        attempt_number=1,
        exhausted=False,
        retry_reason=None,
        storage_key="territory/user/attempt/capture.jpg",
        generation="42",
        content_type="image/jpeg",
        size_bytes=4,
    )
    reads = []
    recorded = []

    async def load(_attempt_id):
        assert _attempt_id == attempt_id
        return evidence

    class Storage:
        def read_bytes(self, storage_key, *, generation, max_bytes):
            reads.append((storage_key, generation, max_bytes))
            return b"jpeg"

    class Vision:
        provider_name = "fake-vlm"
        model_version = "fake-vlm:contract-v1"

        async def classify(self, *, photo, content_type):
            return TerritoryVisionResult("verified", "dog_visible")

    @asynccontextmanager
    async def session_factory():
        yield object()

    async def record(session, persisted_id, **kwargs):
        recorded.append((persisted_id, kwargs))

    from daengs_backend.core import database
    from daengs_backend.services import territory as territory_service

    monkeypatch.setattr(territory_vision, "_load_attempt_evidence", load)
    monkeypatch.setattr(territory_vision, "get_storage", lambda: Storage())
    monkeypatch.setattr(database, "worker_session", session_factory)
    monkeypatch.setattr(territory_service, "record_vision_decision", record)

    result = await territory_vision.process_attempt(attempt_id, classifier=Vision())
    assert result == TerritoryVisionResult("verified", "dog_visible")
    assert reads == [(evidence.storage_key, "42", 4)]
    assert recorded == [
        (
            attempt_id,
            {
                "decision": "verified",
                "model": "fake-vlm",
                "model_version": "fake-vlm:contract-v1",
                "reason": "dog_visible",
                "lease_token": evidence.token,
                "generation": evidence.generation,
            },
        )
    ]


async def test_worker_rejects_storage_generation_change_before_model_call(monkeypatch):
    evidence = VisionLease(
        token=uuid.uuid4(),
        attempt_number=1,
        exhausted=False,
        retry_reason=None,
        storage_key="territory/user/attempt/capture.jpg",
        generation="42",
        content_type="image/jpeg",
        size_bytes=4,
    )

    async def load(_attempt_id):
        return evidence

    class ChangedStorage:
        def read_bytes(self, *args, **kwargs):
            raise StorageObjectChangedError("changed")

    monkeypatch.setattr(territory_vision, "_load_attempt_evidence", load)
    monkeypatch.setattr(territory_vision, "get_storage", lambda: ChangedStorage())
    complete = AsyncMock(return_value=True)
    classify = AsyncMock()
    monkeypatch.setattr(territory_vision, "_complete", complete)
    monkeypatch.setattr(GeminiTerritoryVision, "classify", classify)
    assert await territory_vision.process_attempt(uuid.uuid4()) is None
    assert complete.call_args.kwargs == {"decision": "failed", "reason": "photo_generation_changed"}
    classify.assert_not_awaited()


def test_task_accelerates_durable_retry_without_unfenced_failure_write(monkeypatch):
    attempt_id = str(uuid.uuid4())
    processed = []

    def transient(_attempt_id):
        processed.append(_attempt_id)
        raise TerritoryVisionTransientError("vision_provider_unavailable")

    class RetryRaised(Exception):
        pass

    monkeypatch.setattr(territory_vision, "process_attempt_sync", transient)
    monkeypatch.setattr(
        territory_task.verify_photo,
        "retry",
        lambda **kwargs: (_ for _ in ()).throw(RetryRaised()),
    )

    with pytest.raises(RetryRaised):
        territory_task.verify_photo.run(attempt_id)
    assert processed == [attempt_id]

    territory_task.verify_photo.push_request(retries=territory_task.MAX_RETRIES)
    try:
        territory_task.verify_photo.run(attempt_id)
    finally:
        territory_task.verify_photo.pop_request()
    assert processed == [attempt_id, attempt_id]


def test_worker_queue_is_late_acknowledged_and_isolated():
    assert territory_task.app.conf.task_default_queue == "territory-vision"
    assert territory_task.app.conf.task_acks_late is True
    assert territory_task.app.conf.task_reject_on_worker_lost is True
    assert territory_task.app.conf.worker_prefetch_multiplier == 1


def test_existing_beat_routes_recovery_to_registered_photo_worker():
    from daengs_life.tasks.celery_app import app as beat

    schedule = beat.conf.beat_schedule["recover-territory-photos"]
    assert schedule["task"] in territory_task.app.tasks
    assert schedule["task"] == territory_task.recover_photos.name
    assert schedule["options"]["queue"] == territory_task.QUEUE_NAME
    assert schedule["options"]["expires"] < schedule["schedule"] == 30
