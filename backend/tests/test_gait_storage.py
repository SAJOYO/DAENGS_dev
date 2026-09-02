"""저장소 구현 — GCS(mock)·LocalBridge(실파일)·object key·cleanup (D-043).

`google-cloud-storage` 없이 돕니다 — GcsStorage 는 그 모듈을 지연 import 하므로
mock 을 `sys.modules` 에 꽂아 검사합니다. LocalBridge 는 실제 임시 디렉터리를 씁니다.
"""

from __future__ import annotations

import sys
import types
import uuid

import pytest

from daengs_backend.core.storage import (
    LocalBridgeStorage,
    NotConfiguredStorage,
    StorageNotConfiguredError,
    build_object_key,
)

PET = uuid.uuid4()


# ── object key — backend 가 만든다 (원칙 6) ────────────────────────────
def test_object_key_is_scoped_to_pet_and_kind():
    k = build_object_key(PET, kind="original", source_file="IMG.MOV")
    assert k.startswith(f"gait/{PET}/original/")
    assert k.endswith(".mov")               # 확장자만 따온다
    assert str(PET) in k


def test_object_key_ignores_path_in_source_file():
    """앱이 준 이름에 경로가 있어도 키에 새지 않습니다 — basename 의 suffix 만."""
    k = build_object_key(PET, kind="overlay", source_file="../../etc/passwd.mp4")
    assert ".." not in k
    assert k.count("/") == 3                # gait / pet / kind / name


def test_object_keys_are_unique():
    a = build_object_key(PET, kind="original", source_file="x.mp4")
    b = build_object_key(PET, kind="original", source_file="x.mp4")
    assert a != b


# ── none ────────────────────────────────────────────────────────────────
def test_not_configured_fails_loudly():
    s = NotConfiguredStorage()
    for call in (
        lambda: s.exists("k"),
        lambda: s.download_url("k", expires_in_seconds=60),
        lambda: s.delete("k"),
        lambda: s.create_upload_ticket(object_key="k", content_type="video/mp4"),
    ):
        with pytest.raises(StorageNotConfiguredError):
            call()


# ── LocalBridge — 실제 파일 왕복 ────────────────────────────────────────
def test_local_bridge_write_read_delete(tmp_path):
    s = LocalBridgeStorage(str(tmp_path), base_url="http://x")
    key = build_object_key(PET, kind="original", source_file="a.mp4")

    assert s.exists(key) is False
    s.write(key, b"hello")
    assert s.exists(key) is True
    assert s.local_path(key).read_bytes() == b"hello"

    ticket = s.create_upload_ticket(object_key=key, content_type="video/mp4")
    assert ticket.upload_url == f"http://x/app/gait/_bridge/upload/{key}"
    assert s.download_url(key, expires_in_seconds=60).endswith(key)

    s.delete(key)
    assert s.exists(key) is False


def test_local_bridge_rejects_escape(tmp_path):
    s = LocalBridgeStorage(str(tmp_path))
    with pytest.raises(StorageNotConfiguredError):
        s.local_path("../../secret")


# ── GcsStorage — google 클라이언트 mock ─────────────────────────────────
class _FakeBlob:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def generate_signed_url(self, **kw):
        return f"https://signed.example/{self.key}?m={kw.get('method')}"

    def exists(self):
        return self.key in self.store

    def upload_from_string(self, data, content_type=None):
        self.store[self.key] = data

    def delete(self, **kw):
        self.store.pop(self.key, None)


class _FakeBucket:
    def __init__(self, store):
        self.store = store

    def blob(self, key):
        return _FakeBlob(self.store, key)


@pytest.fixture()
def gcs(monkeypatch):
    """google.cloud.storage 를 mock 으로 꽂습니다 — 지연 import 라 가능합니다."""
    store: dict[str, bytes] = {}

    fake_mod = types.ModuleType("google.cloud.storage")

    class _Client:
        def bucket(self, name):
            return _FakeBucket(store)

    fake_mod.Client = _Client
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.cloud", types.ModuleType("google.cloud"))
    monkeypatch.setitem(sys.modules, "google.cloud.storage", fake_mod)

    from daengs_backend.core.storage import GcsStorage

    return GcsStorage(bucket="daengs-gait", location="asia-northeast3"), store


def test_gcs_signed_upload_url(gcs):
    storage, _ = gcs
    t = storage.create_upload_ticket(object_key="gait/k/original/x.mp4", content_type="video/mp4")
    assert t.upload_url.startswith("https://signed.example/")
    assert "m=PUT" in t.upload_url
    assert t.headers["Content-Type"] == "video/mp4"


def test_gcs_exists_and_delete(gcs):
    storage, store = gcs
    key = "gait/k/original/x.mp4"
    assert storage.exists(key) is False
    storage.upload_bytes(key, b"v", content_type="video/mp4")
    assert storage.exists(key) is True
    storage.delete(key)
    assert storage.exists(key) is False


def test_gcs_requires_bucket():
    from daengs_backend.core.storage import GcsStorage

    with pytest.raises(StorageNotConfiguredError):
        GcsStorage(bucket="", location="asia-northeast3")


# ── 워커 세션: 이벤트 루프 격리 (2026-09-02 서버 실측 버그) ──────────────
def test_worker_session_makes_a_fresh_engine_each_time(monkeypatch):
    """**태스크마다 새 엔진**이어야 합니다.

    모듈 전역 엔진을 쓰면 풀에 남은 커넥션이 첫 이벤트 루프에 묶여, 워커의 **두 번째**
    태스크가 `got Future attached to a different loop` 로 죽습니다. 첫 태스크는 늘
    성공하기 때문에 테스트로 고정해 두지 않으면 되살아나기 쉬운 종류입니다.
    """
    import asyncio

    from daengs_backend.core import database as db

    made: list[dict] = []
    disposed: list[object] = []

    class _FakeEngine:
        async def dispose(self):
            disposed.append(self)

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    def fake_create(url, **kw):
        made.append(kw)
        return _FakeEngine()

    monkeypatch.setattr(db, "create_async_engine", fake_create)
    monkeypatch.setattr(db, "async_sessionmaker", lambda engine, **kw: _FakeSession)

    async def use_once():
        async with db.worker_session():
            pass

    # 태스크 두 번 = asyncio.run 두 번. 실제 워커가 하는 그대로입니다.
    asyncio.run(use_once())
    asyncio.run(use_once())

    assert len(made) == 2, "엔진을 재사용하면 루프가 갈립니다"
    assert len(disposed) == 2, "루프가 닫히기 전에 dispose 해야 합니다"
    # 커넥션을 풀에 남기지 않는 것이 핵심입니다.
    assert all(kw.get("poolclass") is db.NullPool for kw in made)


# ── get_storage 선택 ────────────────────────────────────────────────────
def test_get_storage_defaults_to_none(monkeypatch):
    from daengs_backend import config as cfg
    from daengs_backend.core import storage as st

    monkeypatch.setattr(cfg.settings, "gait_storage", "none")
    assert isinstance(st.get_storage(), NotConfiguredStorage)


def test_get_storage_local_needs_dir(monkeypatch):
    from daengs_backend import config as cfg
    from daengs_backend.core import storage as st

    monkeypatch.setattr(cfg.settings, "gait_storage", "local")
    monkeypatch.setattr(cfg.settings, "gait_local_storage_dir", "")
    with pytest.raises(StorageNotConfiguredError):
        st.get_storage()
