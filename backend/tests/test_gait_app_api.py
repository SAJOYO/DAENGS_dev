"""`/app/gait/*` — backend 소유 orchestration API (D-043).

DB 에는 붙지 않습니다 — repositories 와 storage 를 바꿔치기하고, 여기서 보는 것은
라우터·서비스의 판단(인증 · 소유권 404 · 상태 전이 · 금지 필드 · 503)입니다.

⚠️ 기존 `/gait/*`(gait-analysis FastAPI) 테스트와 별개입니다 — 저쪽은
   `test_gait_service.py` 등이 보고, 앱 전환(#64) 뒤 단계적으로 사라질 계약입니다.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, current_app_user
from daengs_backend.core.storage import (
    NotConfiguredStorage,
    StorageNotConfiguredError,
    UploadTicket,
)
from daengs_backend.main import app
from daengs_backend.repositories import gait_record as gait_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.services import gait as gait_service

OWNER = uuid.uuid4()
PET = uuid.uuid4()


class FakeSession:
    """commit 횟수만 셉니다 — 진짜 쿼리는 리포지토리 바꿔치기가 가로챕니다."""

    def __init__(self) -> None:
        self.commits = 0
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, obj) -> None:
        # 진짜 세션은 DB 기본값(id·created_at)을 받아 옵니다. 가짜는 채워 줍니다.
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.datetime.now(datetime.timezone.utc)
        if getattr(obj, "status", None) is None:
            obj.status = "PENDING"


def _record(**over):
    """서비스·라우터가 읽는 필드만 가진 기록 대역."""
    base = dict(
        id=uuid.uuid4(), pet_id=PET, status="PENDING",
        quality_status=None, quality_tier=None, gait_filter_version=None,
        captured_at=None, source_file="walk.mp4", note=None,
        created_at=datetime.datetime.now(datetime.timezone.utc),
        original_storage_key="gait/x", overlay_storage_key=None,
        quality=None, summary_for_ui=None, video_meta=None,
        failure_reason=None, deleted_at=None,
    )
    base.update(over)
    return type("R", (), base)()


class FakeStorage:
    """전부 성공하는 저장소 — #78 이후를 흉내 냅니다."""

    def __init__(self, exists: bool = True) -> None:
        self._exists = exists

    def create_upload_ticket(self, *, object_key, content_type):
        return UploadTicket(
            storage_key=object_key, upload_url="https://storage.example/put",
            headers={"Content-Type": content_type}, expires_in_seconds=900,
        )

    def exists(self, storage_key):
        return self._exists

    def download_url(self, storage_key, *, expires_in_seconds):
        return "https://storage.example/get"

    def delete(self, storage_key):
        pass


@pytest.fixture()
def client(monkeypatch):
    session = FakeSession()
    app.dependency_overrides[current_app_user] = lambda: AppPrincipal(app_user_id=OWNER)
    app.dependency_overrides[get_session] = lambda: session
    monkeypatch.setattr(gait_service, "get_storage", lambda: FakeStorage())
    # 큐 발행은 브로커가 필요하니 막습니다 — 발행 여부만 봅니다.
    sent: list[str] = []

    import daengs_backend.tasks.gait as gait_tasks

    monkeypatch.setattr(gait_tasks.analyze, "delay", lambda rid: sent.append(rid))
    monkeypatch.setattr(gait_tasks.cleanup, "delay", lambda rid: None)
    c = TestClient(app)
    c.fake_session = session
    c.sent_jobs = sent
    yield c
    app.dependency_overrides.clear()


# ── 인증 ───────────────────────────────────────────────────────────────
def test_all_endpoints_require_auth():
    """토큰 없이 부르면 전부 거절 — 무인증이던 /gait/* 와의 결정적 차이입니다."""
    app.dependency_overrides.clear()
    c = TestClient(app)
    rid = uuid.uuid4()
    assert c.post("/app/gait/analyze", json={}).status_code in (401, 403)
    assert c.get(f"/app/gait/records?pet_id={PET}").status_code in (401, 403)
    assert c.get(f"/app/gait/records/{rid}").status_code in (401, 403)
    assert c.delete(f"/app/gait/records/{rid}").status_code in (401, 403)
    assert c.post(f"/app/gait/records/{rid}/confirm").status_code in (401, 403)


# ── analyze ────────────────────────────────────────────────────────────
def _analyze_body():
    return {"pet_id": str(PET), "source_file": "walk.mp4"}


def test_analyze_rejects_unowned_pet(client, monkeypatch):
    """남의 강아지 = 없는 강아지 — 같은 404 입니다 (pet 라우터와 같은 규칙)."""
    async def none(session, app_user_id, pet_id):
        return None

    monkeypatch.setattr(pet_repo, "get_owned", none)
    assert client.post("/app/gait/analyze", json=_analyze_body()).status_code == 404


def test_analyze_creates_pending_and_ticket(client, monkeypatch):
    async def owned(session, app_user_id, pet_id):
        assert app_user_id == OWNER          # 소유권이 토큰의 주인으로 확인되는지
        return object()

    monkeypatch.setattr(pet_repo, "get_owned", owned)
    r = client.post("/app/gait/analyze", json=_analyze_body())
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "PENDING"
    assert body["upload_url"].startswith("https://")
    assert client.fake_session.commits == 1
    assert client.sent_jobs == []            # confirm 전에는 발행하지 않습니다


def test_analyze_returns_503_when_storage_not_configured(client, monkeypatch):
    """#78 전의 실제 상태 — 기록을 만들기 **전에** 실패해야 쓰레기 PENDING 이 안 남습니다."""
    async def owned(session, app_user_id, pet_id):
        return object()

    monkeypatch.setattr(pet_repo, "get_owned", owned)
    monkeypatch.setattr(gait_service, "get_storage", lambda: NotConfiguredStorage())
    r = client.post("/app/gait/analyze", json=_analyze_body())
    assert r.status_code == 503
    assert client.fake_session.commits == 0   # 기록이 만들어지지 않았습니다
    assert client.fake_session.added == []


def test_analyze_rejects_non_uuid_pet_id(client):
    """dog_id="1" 같은 값이 들어오던 자리 — pydantic 이 422 로 거릅니다."""
    r = client.post("/app/gait/analyze", json={"pet_id": "1", "source_file": "a.mp4"})
    assert r.status_code == 422


# ── confirm ────────────────────────────────────────────────────────────
def test_confirm_enqueues_after_upload(client, monkeypatch):
    rec = _record(status="PENDING")

    async def owned(session, app_user_id, record_id):
        return rec

    monkeypatch.setattr(gait_repo, "get_owned", owned)
    r = client.post(f"/app/gait/records/{rec.id}/confirm")
    assert r.status_code == 200
    assert r.json()["status"] == "UPLOADED"
    assert client.sent_jobs == [str(rec.id)]  # commit 뒤 발행


def test_confirm_wrong_state_is_409(client, monkeypatch):
    """재전달·중복 confirm — 두 번 발행되면 안 됩니다."""
    rec = _record(status="DONE")

    async def owned(session, app_user_id, record_id):
        return rec

    monkeypatch.setattr(gait_repo, "get_owned", owned)
    assert client.post(f"/app/gait/records/{rec.id}/confirm").status_code == 409
    assert client.sent_jobs == []


def test_confirm_rejects_when_file_missing(client, monkeypatch):
    """앱의 말만 믿지 않습니다 — 실존 확인이 실패하면 발행하지 않습니다."""
    rec = _record(status="PENDING")

    async def owned(session, app_user_id, record_id):
        return rec

    monkeypatch.setattr(gait_repo, "get_owned", owned)
    monkeypatch.setattr(gait_service, "get_storage", lambda: FakeStorage(exists=False))
    assert client.post(f"/app/gait/records/{rec.id}/confirm").status_code == 409
    assert client.sent_jobs == []


# ── 조회·삭제 ──────────────────────────────────────────────────────────
def test_get_and_delete_unowned_are_404(client, monkeypatch):
    async def none(session, app_user_id, record_id):
        return None

    monkeypatch.setattr(gait_repo, "get_owned", none)
    rid = uuid.uuid4()
    assert client.get(f"/app/gait/records/{rid}").status_code == 404
    assert client.delete(f"/app/gait/records/{rid}").status_code == 404


def test_list_limit_bounds_and_stale_cursor(client, monkeypatch):
    async def boom(session, app_user_id, pet_id, *, limit, cursor):
        raise LookupError("cursor")

    monkeypatch.setattr(gait_repo, "list_for_pet", boom)
    assert (
        client.get(f"/app/gait/records?pet_id={PET}&limit=0").status_code == 400
    )
    assert (
        client.get(f"/app/gait/records?pet_id={PET}&cursor={uuid.uuid4()}").status_code
        == 400
    )


def test_list_builds_next_cursor_from_overfetch(client, monkeypatch):
    rows = [_record(status="DONE", quality_status="ok") for _ in range(3)]

    async def listing(session, app_user_id, pet_id, *, limit, cursor):
        return rows  # limit+1 개를 돌려주는 계약 — limit=2 면 3개

    monkeypatch.setattr(gait_repo, "list_for_pet", listing)
    body = client.get(f"/app/gait/records?pet_id={PET}&limit=2").json()
    assert len(body["records"]) == 2
    assert body["next_cursor"] == str(rows[1].id)
    assert body["records"][0]["comparable"] is True


# ── 금지 필드 ──────────────────────────────────────────────────────────
def test_detail_never_exposes_internal_feature_vector(client, monkeypatch):
    """스키마가 그 필드를 아예 모르므로 **실수로도** 못 내보냅니다 — 모델 대역에
    값을 채워 두고, 응답에 안 나오는 것을 봅니다."""
    rec = _record(
        status="DONE", quality_status="ok",
        internal_feature_vector={"f0": 1.0},
        summary_for_ui={"hip": {"x_range": 1.0}},
    )

    async def owned(session, app_user_id, record_id):
        return rec

    monkeypatch.setattr(gait_repo, "get_owned", owned)
    body = client.get(f"/app/gait/records/{rec.id}").json()
    assert "internal_feature_vector" not in body
    assert not any("_dev_only" in k for k in body)
    assert body["summary_for_ui"] == {"hip": {"x_range": 1.0}}


# ── 워커 경계 (ⓒ) ──────────────────────────────────────────────────────
def test_task_module_imports_without_gait_deps():
    """태스크 모듈 import 가 torch·daengs_gait 를 끌고 오면 안 됩니다 — backend 웹
    프로세스(기본 설치)가 이 모듈로 `.delay()` 를 부르기 때문입니다."""
    import sys

    import daengs_backend.tasks.gait  # noqa: F401

    assert "daengs_gait.pipeline" not in sys.modules
    assert "torch" not in sys.modules


# ── 임시 bridge 의 자격 검사 (2026-09-02 서버에서 무인증으로 열려 있었습니다) ──
@pytest.fixture()
def bridge(client, monkeypatch, tmp_path):
    """gait_storage=local 로 만들어 bridge 엔드포인트를 켭니다."""
    from daengs_backend.core.storage import LocalBridgeStorage
    from daengs_backend.routers import gait as gait_router

    storage = LocalBridgeStorage(str(tmp_path), base_url="")
    monkeypatch.setattr(gait_router, "_local_bridge", lambda: storage)
    return storage


def test_bridge_upload_rejects_unissued_key(bridge, client, monkeypatch):
    """**임의 경로로 디스크를 채울 수 없어야 합니다.** 인증 헤더가 없는 자리라,
    발급된 적 없는 키를 받아 주면 아무나 서버에 파일을 쌓을 수 있습니다."""
    async def none(session, storage_key, *, status=None):
        return None

    monkeypatch.setattr(gait_repo, "find_by_storage_key", none)
    r = client.put("/app/gait/_bridge/upload/gait/x/original/attacker.mp4", content=b"junk")
    assert r.status_code == 404
    assert not list(bridge.local_path("").rglob("*.mp4"))  # 아무것도 안 쓰였습니다


def test_bridge_upload_accepts_issued_pending_key(bridge, client, monkeypatch):
    key = "gait/pet/original/abc.mp4"
    seen = {}

    async def found(session, storage_key, *, status=None):
        seen["key"], seen["status"] = storage_key, status
        return _record(status="PENDING", original_storage_key=key)

    monkeypatch.setattr(gait_repo, "find_by_storage_key", found)
    r = client.put(f"/app/gait/_bridge/upload/{key}", content=b"video-bytes")
    assert r.status_code == 200
    # PENDING 으로 좁혀 찾습니다 — confirm 뒤 같은 키 덮어쓰기가 막히는 근거입니다.
    assert seen == {"key": key, "status": "PENDING"}
    assert bridge.local_path(key).read_bytes() == b"video-bytes"


def test_bridge_download_rejects_unissued_key(bridge, client, monkeypatch):
    async def none(session, storage_key, *, status=None):
        return None

    monkeypatch.setattr(gait_repo, "find_by_storage_key", none)
    assert client.get("/app/gait/_bridge/download/gait/x/original/a.mp4").status_code == 404


def test_storage_not_configured_fails_loudly():
    """미설정 저장소는 no-op 이 아니라 명확한 실패입니다 — 조용히 성공하면
    confirm 이 거짓말을 하고 워커가 없는 파일을 받으러 갑니다."""
    s = NotConfiguredStorage()
    with pytest.raises(StorageNotConfiguredError):
        s.exists("k")
    with pytest.raises(StorageNotConfiguredError):
        s.create_upload_ticket(object_key="k", content_type="video/mp4")
