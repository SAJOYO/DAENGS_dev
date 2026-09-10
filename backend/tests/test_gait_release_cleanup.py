"""PR #137 release blocker: pet FK cascade 전에 gait storage 를 안전하게 정리합니다."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest

from daengs_backend.core.storage import build_overlay_object_key
from daengs_backend.models.gait_record import GaitRecord
from daengs_backend.services import gait as gait_service
from daengs_backend.services import pet as pet_service


class FakeStorage:
    def __init__(self, objects: set[str] | None = None, *, fail_on: str | None = None):
        self.objects = set(objects or ())
        self.fail_on = fail_on
        self.deleted: list[str] = []
        self.uploaded: list[str] = []

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        if key == self.fail_on:
            raise RuntimeError("storage unavailable")
        # 없는 object 도 성공입니다.
        self.objects.discard(key)

    def upload_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        self.uploaded.append(key)
        self.objects.add(key)


class TxSession:
    def __init__(self, *, fail_commit_once: bool = False):
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self.deletes = 0
        self.fail_commit_once = fail_commit_once

    async def commit(self) -> None:
        self.commits += 1
        if self.fail_commit_once:
            self.fail_commit_once = False
            raise RuntimeError("commit failed")

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def flush(self) -> None:
        self.flushes += 1

    async def delete(self, row) -> None:
        self.deletes += 1


def _record(*, original: str = "original", overlay: str | None = "overlay") -> GaitRecord:
    return GaitRecord(
        id=uuid.uuid4(),
        pet_id=uuid.uuid4(),
        status="DONE",
        original_storage_key=original,
        overlay_storage_key=overlay,
    )


async def test_cleanup_retains_keys_until_original_and_overlay_are_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record()
    expected = build_overlay_object_key(record.pet_id, record.id)
    storage = FakeStorage({"original", "overlay", expected})
    observed_rows: list[tuple[str | None, str | None]] = []

    async def locked(session, pet_ids):
        assert pet_ids == [record.pet_id]
        return [record]

    original_delete = storage.delete

    def delete_while_row_is_retained(key: str) -> None:
        observed_rows.append(
            (record.original_storage_key, record.overlay_storage_key)
        )
        original_delete(key)

    storage.delete = delete_while_row_is_retained  # type: ignore[method-assign]
    monkeypatch.setattr(gait_service.gait_repo, "list_for_pets_for_update", locked)
    monkeypatch.setattr(gait_service, "get_storage", lambda: storage)

    rows = await gait_service.cleanup_for_pets(object(), [record.pet_id])

    assert rows == [record]
    assert observed_rows == [("original", "overlay")] * 3
    assert storage.deleted == ["original", "overlay", expected]
    assert storage.objects == set()


async def test_cleanup_accepts_already_missing_objects_and_retry_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record()
    storage = FakeStorage()

    async def locked(session, pet_ids):
        return [record]

    monkeypatch.setattr(gait_service.gait_repo, "list_for_pets_for_update", locked)
    monkeypatch.setattr(gait_service, "get_storage", lambda: storage)

    await gait_service.cleanup_for_pets(object(), [record.pet_id])
    await gait_service.cleanup_for_pets(object(), [record.pet_id])

    expected = build_overlay_object_key(record.pet_id, record.id)
    assert storage.deleted == ["original", "overlay", expected] * 2


async def test_storage_success_then_db_commit_failure_keeps_retry_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record()
    expected = build_overlay_object_key(record.pet_id, record.id)
    storage = FakeStorage({"original", "overlay", expected})
    session = TxSession(fail_commit_once=True)

    async def owned(session, owner, record_id, **kwargs):
        assert kwargs == {"for_update": True}
        return record

    monkeypatch.setattr(gait_service.gait_repo, "get_owned", owned)
    monkeypatch.setattr(gait_service, "get_storage", lambda: storage)

    with pytest.raises(RuntimeError, match="commit failed"):
        await gait_service.soft_delete(session, uuid.uuid4(), record.id)
    assert storage.objects == set()
    assert session.rollbacks == 1

    # DB rollback 이 행과 키를 보존했다고 가정한 실제 재진입입니다. object 는 이미
    # 없지만 두 번째 삭제가 성공하고 DB commit 까지 끝납니다.
    await gait_service.soft_delete(session, uuid.uuid4(), record.id)

    assert session.commits == 2
    assert session.deletes == 2


async def test_storage_failure_does_not_reach_walk_or_pet_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pet = type("Pet", (), {"id": uuid.uuid4()})()
    session = TxSession()
    destructive_calls: list[str] = []

    async def owned(session, owner, pet_id, **kwargs):
        return pet

    async def storage_failure(session, pet_ids):
        raise RuntimeError("storage unavailable")

    async def destructive(*args, **kwargs):
        destructive_calls.append("called")

    monkeypatch.setattr(pet_service.pet_repo, "get_owned", owned)
    monkeypatch.setattr(pet_service.gait_service, "cleanup_for_pets", storage_failure)
    monkeypatch.setattr(pet_service.walk_repo, "delete_walks_only_with", destructive)
    monkeypatch.setattr(pet_service.pet_repo, "delete", destructive)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await pet_service.delete_pet(session, uuid.uuid4(), pet.id)

    assert destructive_calls == []
    assert session.commits == 0
    assert session.rollbacks == 1


async def test_single_pet_deletion_uses_common_gait_cleanup_before_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 사진 칸도 들고 있어야 합니다 — 삭제 경로가 보행 객체와 **프로필 사진**을
    # 둘 다 훑기 때문입니다 (D-052). 여기서는 사진이 없는 아이라 전부 None 입니다.
    pet = type(
        "Pet", (), {"id": uuid.uuid4(), "photo_storage_key": None, "photo_pending_key": None}
    )()
    session = TxSession()
    events: list[str] = []

    async def owned(session, owner, pet_id, **kwargs):
        assert kwargs == {"for_update": True}
        return pet

    async def cleanup(session, pet_ids):
        events.append("gait")
        assert pet_ids == [pet.id]

    async def delete_walks(session, pet_id):
        events.append("walks")

    async def delete_pet_row(session, row):
        events.append("pet")

    async def no_user(session, owner):
        return None

    async def no_carers(session, pet_id):
        # 이 아이는 혼자 돌본다. 삭제 경로는 돌보미의 `primary_pet_id` 도 수선하므로
        # (docs/co-care.md §3) 그 조회를 여기서 비워 둡니다 — `TxSession` 은 쿼리를
        # 못 받고, 이 테스트가 재는 것은 gait·walk·pet 의 **순서**입니다.
        return []

    monkeypatch.setattr(pet_service.pet_repo, "get_owned", owned)
    monkeypatch.setattr(pet_service.gait_service, "cleanup_for_pets", cleanup)
    monkeypatch.setattr(pet_service.walk_repo, "delete_walks_only_with", delete_walks)
    monkeypatch.setattr(pet_service.app_user_repo, "get_by_id", no_user)
    monkeypatch.setattr(pet_service.member_repo, "list_members", no_carers)
    monkeypatch.setattr(pet_service.pet_repo, "delete", delete_pet_row)

    await pet_service.delete_pet(session, uuid.uuid4(), pet.id)

    assert events == ["gait", "walks", "pet"]
    assert session.commits == 1


async def test_processing_completion_cannot_upload_after_cleanup_deleted_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record(overlay=None)
    record.status = "UPLOADED"
    storage = FakeStorage()

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class WorkerSession:
        def __init__(self):
            self.selects = 0

        async def execute(self, stmt):
            self.selects += 1
            # 첫 잠금은 UPLOADED 행을 PROCESSING 으로 전이합니다. 분석하는 동안 pet
            # cleanup 이 commit 해 FK CASCADE 로 행을 없앤 상황을 두 번째 None 이 재현합니다.
            return Result(record if self.selects == 1 else None)

        async def commit(self) -> None:
            pass

    worker = WorkerSession()

    @asynccontextmanager
    async def worker_session():
        yield worker

    from daengs_backend.core import database

    monkeypatch.setattr(database, "worker_session", worker_session)
    monkeypatch.setattr(
        gait_service,
        "_analyze_from_storage",
        lambda key: {"_overlay_bytes": b"new overlay"},
    )
    monkeypatch.setattr(gait_service, "get_storage", lambda: storage)

    await gait_service._run_analysis(record.id)

    assert record.status == "PROCESSING"
    assert storage.uploaded == []


def test_worker_requests_nonpersistent_pipeline_and_returns_overlay_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import sys
    import types

    source = tmp_path / "source.bin"
    source.write_bytes(b"video")
    artifact_dirs = []

    class LocalStorage:
        def local_path(self, key):
            return source

    def process_video(path, *, persist):
        assert persist is False
        artifact_dirs.append(path.parent)
        overlay = path.parent / "input_overlay.mp4"
        overlay.write_bytes(b"overlay")
        return {
            "record_id": None,
            "quality": {"status": "ok", "quality_tier": "good"},
            "overlay_video": str(overlay),
        }

    fake_pipeline = types.ModuleType("daengs_gait.pipeline")
    fake_pipeline.process_video = process_video
    monkeypatch.setitem(sys.modules, "daengs_gait.pipeline", fake_pipeline)
    from daengs_backend.core import storage as storage_module

    monkeypatch.setattr(storage_module, "get_storage", lambda: LocalStorage())

    result = gait_service._analyze_from_storage("original")

    assert result["_overlay_bytes"] == b"overlay"
    assert artifact_dirs and not artifact_dirs[0].exists()
