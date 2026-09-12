"""점령지 방문 인증의 앱 API·상태 전이 계약."""

from __future__ import annotations

import datetime
import os
import uuid
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, current_app_user
from daengs_backend.core.storage import LocalBridgeStorage, StoredObject, UploadTicket
from daengs_backend.main import app
from daengs_backend.models.territory import TerritoryAttempt
from daengs_backend.repositories import territory as territory_repo
from daengs_backend.services import territory as territory_service
from daengs_backend.services.territory_site_lookup import (
    HttpTerritorySiteLookup,
    TerritorySiteSnapshot,
    TerritorySiteUnavailableError,
    get_territory_site_lookup,
)

OWNER = uuid.uuid4()
CAPTURE = uuid.uuid4()
SESSION = uuid.uuid4()
SITE_ID = "territory-site:hex-v1:140:324:777"
NOW = datetime.datetime(2026, 9, 3, 3, 0, tzinfo=datetime.UTC)
from tests.territory.support.paths import REPO


@pytest.fixture(autouse=True)
def unbound_photo_visits(monkeypatch):
    # These tests exercise legacy visits. Shared ownership uses a real PostgreSQL suite.
    from daengs_backend.repositories import territory_claim

    async def no_binding(*args):
        return None

    monkeypatch.setattr(territory_claim, "photo_binding", no_binding)


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, obj: TerritoryAttempt) -> None:
        if obj.created_at is None:
            obj.created_at = NOW
        if obj.updated_at is None:
            obj.updated_at = NOW


class FakeLookup:
    def __init__(self, *, lat: str = "37.5000000", lng: str = "127.0000000") -> None:
        self.site = TerritorySiteSnapshot(SITE_ID, Decimal(lat), Decimal(lng))
        self.calls = 0

    async def find_near_capture(self, **kwargs):
        self.calls += 1
        return self.site if kwargs["site_id"] == SITE_ID else None


class FakeStorage:
    def __init__(
        self,
        *,
        exists: bool = True,
        size_bytes: int = 4,
        content_type: str | None = "image/jpeg",
    ) -> None:
        self._exists = exists
        self.object = StoredObject("generation-1", size_bytes, content_type)
        self.redacted: list[tuple[str, str]] = []

    def create_upload_ticket(
        self, *, object_key, content_type, bridge_upload_path=None, create_only=False
    ):
        assert bridge_upload_path == "/app/territory/attempts/_bridge/upload"
        assert create_only is True
        return UploadTicket(
            storage_key=object_key,
            upload_url=f"https://storage.example/{object_key}",
            headers={
                "Content-Type": content_type,
                "x-goog-if-generation-match": "0",
            },
            expires_in_seconds=900,
        )

    def stat(self, storage_key):
        return self.object if self._exists else None

    def read_bytes(self, storage_key, *, generation, max_bytes):
        assert generation == self.object.generation
        return b"jpeg"[:max_bytes]

    def redact(self, storage_key, *, generation):
        self.redacted.append((storage_key, generation))
        return "redacted-generation"


def _body(**overrides):
    body = {
        "client_capture_id": str(CAPTURE),
        "client_session_id": str(SESSION),
        "site_id": SITE_ID,
        "captured_at": NOW.isoformat(),
        "lat": "37.5000000",
        "lng": "127.0000000",
        "accuracy_m": 4.2,
        "is_mock": False,
        "content_type": "image/jpeg",
    }
    body.update(overrides)
    return body


def _attempt(**overrides) -> TerritoryAttempt:
    values = {
        "id": uuid.uuid4(),
        "app_user_id": OWNER,
        "client_capture_id": CAPTURE,
        "client_session_id": SESSION,
        "site_id": SITE_ID,
        "captured_at": NOW,
        "capture_lat": Decimal("37.5000000"),
        "capture_lng": Decimal("127.0000000"),
        "accuracy_m": 4.2,
        "is_mock": False,
        "site_lat": Decimal("37.5000000"),
        "site_lng": Decimal("127.0000000"),
        "distance_m": 0.0,
        "status": "PENDING_UPLOAD",
        "photo_storage_key": f"territory/{OWNER}/{uuid.uuid4()}/capture.jpg",
        "photo_content_type": "image/jpeg",
        "photo_object_generation": None,
        "photo_size_bytes": None,
        "photo_redacted_at": None,
        "vision_model": None,
        "vision_model_version": None,
        "decision_reason": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return TerritoryAttempt(**values)


@pytest.fixture()
def client(monkeypatch):
    session = FakeSession()
    lookup = FakeLookup()
    storage = FakeStorage()
    app.dependency_overrides[current_app_user] = lambda: AppPrincipal(app_user_id=OWNER)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_territory_site_lookup] = lambda: lookup
    monkeypatch.setattr(territory_service, "get_storage", lambda: storage)
    published: list[uuid.UUID] = []
    monkeypatch.setattr(territory_service, "_publish_vision_attempt", published.append)

    async def no_existing(*args, **kwargs):
        return None

    monkeypatch.setattr(territory_repo, "get_by_client_capture", no_existing)
    c = TestClient(app)
    c.fake_session = session
    c.fake_lookup = lookup
    c.fake_storage = storage
    c.published_vision_attempts = published
    yield c
    app.dependency_overrides.clear()


def test_endpoints_require_app_authentication():
    app.dependency_overrides.clear()
    client = TestClient(app)
    attempt_id = uuid.uuid4()
    assert client.post("/app/territory/attempts", json=_body()).status_code in (401, 403)
    assert client.get(f"/app/territory/attempts/{attempt_id}").status_code in (401, 403)
    assert client.post(f"/app/territory/attempts/{attempt_id}/confirm").status_code in (401, 403)


def test_start_checks_current_site_and_returns_direct_upload_ticket(client):
    response = client.post("/app/territory/attempts", json=_body())
    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "PENDING_UPLOAD"
    assert payload["distance_m"] == pytest.approx(0)
    assert payload["upload_url"].startswith("https://storage.example/territory/")
    assert payload["upload_headers"] == {
        "Content-Type": "image/jpeg",
        "x-goog-if-generation-match": "0",
    }
    assert client.fake_lookup.calls == 1
    assert client.fake_session.commits == 1


def test_start_rejects_outside_ten_metres(client):
    response = client.post(
        "/app/territory/attempts",
        json=_body(lat="37.5002000"),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "outside_capture_radius"
    assert client.fake_session.added == []


def test_start_requires_uncertainty_to_fit_inside_ten_metres(client):
    response = client.post(
        "/app/territory/attempts",
        json=_body(lat="37.5000450", accuracy_m=6.0),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "insufficient_location_accuracy"


def test_start_requires_location_accuracy(client):
    body = _body()
    del body["accuracy_m"]
    assert client.post("/app/territory/attempts", json=body).status_code == 422


def test_mock_location_is_rejected_without_gameboard_lookup(client):
    response = client.post("/app/territory/attempts", json=_body(is_mock=True))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "mock_location"
    assert client.fake_lookup.calls == 0


def test_capture_coordinates_must_fit_the_persisted_precision(client):
    response = client.post(
        "/app/territory/attempts",
        json=_body(lat="37.50000001"),
    )
    assert response.status_code == 422


def test_same_capture_retry_returns_existing_attempt_and_200(client, monkeypatch):
    existing = _attempt()

    async def found(*args, **kwargs):
        return existing

    monkeypatch.setattr(territory_repo, "get_by_client_capture", found)
    response = client.post("/app/territory/attempts", json=_body())
    assert response.status_code == 200
    assert response.json()["attempt_id"] == str(existing.id)
    assert client.fake_lookup.calls == 0
    assert client.fake_session.commits == 0


def test_same_capture_id_with_changed_evidence_is_conflict(client, monkeypatch):
    existing = _attempt(capture_lat=Decimal("37.4990000"))

    async def found(*args, **kwargs):
        return existing

    monkeypatch.setattr(territory_repo, "get_by_client_capture", found)
    response = client.post("/app/territory/attempts", json=_body())
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "capture_id_conflict"


def test_confirm_checks_photo_then_becomes_vision_pending(client, monkeypatch):
    attempt = _attempt()

    async def owned(*args, **kwargs):
        assert kwargs["for_update"] is True
        return attempt

    monkeypatch.setattr(territory_repo, "get_owned", owned)
    response = client.post(f"/app/territory/attempts/{attempt.id}/confirm")
    assert response.status_code == 200
    assert response.json()["status"] == "VISION_PENDING"
    assert attempt.photo_object_generation == "generation-1"
    assert attempt.photo_size_bytes == 4
    assert client.fake_session.commits == 1
    assert client.published_vision_attempts == [attempt.id]


def test_confirm_retry_republishes_pending_attempt(client, monkeypatch):
    attempt = _attempt(
        status="VISION_PENDING",
        photo_object_generation="generation-1",
        photo_size_bytes=4,
    )

    async def owned(*args, **kwargs):
        return attempt

    monkeypatch.setattr(territory_repo, "get_owned", owned)
    response = client.post(f"/app/territory/attempts/{attempt.id}/confirm")
    assert response.status_code == 200
    assert response.json()["status"] == "VISION_PENDING"
    assert client.published_vision_attempts == [attempt.id]
    assert client.fake_session.commits == 1


def test_confirm_keeps_pending_state_when_queue_is_unavailable(client, monkeypatch):
    attempt = _attempt()

    async def owned(*args, **kwargs):
        return attempt

    def unavailable(*args, **kwargs):
        raise territory_service.TerritoryVisionQueueUnavailable("queue unavailable")

    monkeypatch.setattr(territory_repo, "get_owned", owned)
    monkeypatch.setattr(territory_service, "_publish_vision_attempt", unavailable)
    response = client.post(f"/app/territory/attempts/{attempt.id}/confirm")
    assert response.status_code == 503
    assert attempt.status == "VISION_PENDING"
    assert client.fake_session.commits == 1


def test_confirm_missing_photo_is_conflict(client, monkeypatch):
    attempt = _attempt()

    async def owned(*args, **kwargs):
        return attempt

    monkeypatch.setattr(territory_repo, "get_owned", owned)
    client.fake_storage._exists = False
    response = client.post(f"/app/territory/attempts/{attempt.id}/confirm")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "photo_not_uploaded"
    assert attempt.status == "PENDING_UPLOAD"


def test_confirm_rejects_oversized_photo(client, monkeypatch):
    attempt = _attempt()

    async def owned(*args, **kwargs):
        return attempt

    monkeypatch.setattr(territory_repo, "get_owned", owned)
    client.fake_storage.object = StoredObject(
        "generation-1",
        territory_service.MAX_TERRITORY_PHOTO_BYTES + 1,
        "image/jpeg",
    )
    response = client.post(f"/app/territory/attempts/{attempt.id}/confirm")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "invalid_photo_size"
    assert attempt.status == "PENDING_UPLOAD"


@pytest.mark.parametrize(
    ("decision", "expected_status", "visit_count"),
    [("verified", "VERIFIED", 1), ("rejected", "REJECTED", 0), ("failed", "FAILED", 0)],
)
async def test_vision_decision_is_terminal_and_deletes_photo(
    monkeypatch, decision, expected_status, visit_count
):
    session = FakeSession()
    storage = FakeStorage()
    attempt = _attempt(
        status="VISION_PENDING",
        photo_object_generation="generation-1",
        photo_size_bytes=4,
    )

    async def for_decision(*args, **kwargs):
        return attempt

    monkeypatch.setattr(territory_repo, "get_for_decision", for_decision)
    monkeypatch.setattr(territory_service, "get_storage", lambda: storage)
    result = await territory_service.record_vision_decision(
        session,
        attempt.id,
        decision=decision,
        model="dog-detector",
        model_version="2026-09-03",
        reason="dog" if decision == "verified" else "not_dog",
    )
    assert result.status == expected_status
    assert result.photo_redacted_at is not None
    assert storage.redacted == [(attempt.photo_storage_key, "generation-1")]
    assert session.commits == 2
    assert (
        len([item for item in session.added if item.__class__.__name__ == "VerifiedVisit"])
        == visit_count
    )


async def test_vision_decision_survives_photo_cleanup_failure(monkeypatch):
    session = FakeSession()
    attempt = _attempt(
        status="VISION_PENDING",
        photo_object_generation="generation-1",
        photo_size_bytes=4,
    )

    class FailingOnceStorage(FakeStorage):
        failed = False

        def redact(self, storage_key, *, generation):
            if not self.failed:
                self.failed = True
                raise OSError("storage unavailable")
            return super().redact(storage_key, generation=generation)

    async def for_decision(*args, **kwargs):
        return attempt

    storage = FailingOnceStorage()
    monkeypatch.setattr(territory_repo, "get_for_decision", for_decision)
    monkeypatch.setattr(territory_service, "get_storage", lambda: storage)
    with pytest.raises(OSError, match="storage unavailable"):
        await territory_service.record_vision_decision(
            session,
            attempt.id,
            decision="verified",
            model="dog-detector",
            model_version="2026-09-03",
        )

    assert attempt.status == "VERIFIED"
    assert attempt.photo_redacted_at is None
    assert session.commits == 1

    result = await territory_service.record_vision_decision(
        session,
        attempt.id,
        decision="verified",
        model="dog-detector",
        model_version="2026-09-03",
    )
    assert result.photo_redacted_at is not None
    assert storage.redacted == [(attempt.photo_storage_key, "generation-1")]
    assert session.commits == 2
    assert len([item for item in session.added if item.__class__.__name__ == "VerifiedVisit"]) == 1


def test_local_bridge_accepts_only_issued_matching_small_photo(client, monkeypatch, tmp_path):
    from daengs_backend.routers import territory as territory_router

    storage = LocalBridgeStorage(str(tmp_path), base_url="http://testserver")
    key = f"territory/{OWNER}/{uuid.uuid4()}/capture.jpg"
    attempt = _attempt(photo_storage_key=key)

    async def found(*args, **kwargs):
        return attempt

    monkeypatch.setattr(territory_router, "_local_bridge", lambda: storage)
    monkeypatch.setattr(territory_repo, "find_pending_by_storage_key", found)
    response = client.put(
        f"/app/territory/attempts/_bridge/upload/{key}",
        content=b"jpeg",
        headers={"Content-Type": "image/jpeg"},
    )
    assert response.status_code == 200
    assert storage.local_path(key).read_bytes() == b"jpeg"

    overwrite = client.put(
        f"/app/territory/attempts/_bridge/upload/{key}",
        content=b"other jpeg",
        headers={"Content-Type": "image/jpeg"},
    )
    assert overwrite.status_code == 409
    assert storage.local_path(key).read_bytes() == b"jpeg"

    wrong_type = client.put(
        f"/app/territory/attempts/_bridge/upload/{key}",
        content=b"webp",
        headers={"Content-Type": "image/webp"},
    )
    assert wrong_type.status_code == 415


def test_failed_bridge_upload_can_be_retried_then_confirmed(client, monkeypatch, tmp_path):
    from daengs_backend.routers import territory as territory_router

    storage = LocalBridgeStorage(str(tmp_path))
    attempt = _attempt()
    key = attempt.photo_storage_key
    photo = b"complete jpeg bytes"

    async def found(*args, **kwargs):
        return attempt

    def failed_sync(*args):
        raise OSError("injected disk sync failure")

    monkeypatch.setattr(territory_router, "_local_bridge", lambda: storage)
    monkeypatch.setattr(territory_service, "get_storage", lambda: storage)
    monkeypatch.setattr(territory_repo, "find_pending_by_storage_key", found)
    monkeypatch.setattr(territory_repo, "get_owned", found)
    upload_url = f"/app/territory/attempts/_bridge/upload/{key}"
    confirm_url = f"/app/territory/attempts/{attempt.id}/confirm"

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", failed_sync)
        # TestClient exposes the underlying exception for an unhandled HTTP 500.
        with pytest.raises(OSError, match="injected disk sync failure"):
            client.put(upload_url, content=photo, headers={"Content-Type": "image/jpeg"})

    missing = client.post(confirm_url)
    assert missing.status_code == 409
    assert missing.json()["detail"]["code"] == "photo_not_uploaded"
    assert attempt.status == "PENDING_UPLOAD"
    assert client.fake_session.commits == 0
    assert client.published_vision_attempts == []

    retry = client.put(upload_url, content=photo, headers={"Content-Type": "image/jpeg"})
    assert retry.status_code == 200
    confirmed = client.post(confirm_url)
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "VISION_PENDING"
    assert attempt.photo_size_bytes == len(photo)
    assert attempt.photo_object_generation == sha256(photo).hexdigest()
    assert client.published_vision_attempts == [attempt.id]


async def test_confirm_during_bridge_write_cannot_see_partial_photo(client, monkeypatch, tmp_path):
    import asyncio

    import httpx

    from daengs_backend.routers import territory as territory_router

    storage = LocalBridgeStorage(str(tmp_path))
    attempt = _attempt()
    key = attempt.photo_storage_key
    photo = b"complete jpeg bytes"
    started = Event()
    resume = Event()
    original_open = Path.open

    class PausedStream:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, data):
            written = self.stream.write(data[:4])
            self.stream.flush()
            started.set()
            if not resume.wait(timeout=5):
                raise TimeoutError("confirm could not proceed while the photo was being written")
            return written + self.stream.write(data[4:])

    def paused_open(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return PausedStream(stream) if mode == "xb" else stream

    async def found(*args, **kwargs):
        return attempt

    monkeypatch.setattr(territory_router, "_local_bridge", lambda: storage)
    monkeypatch.setattr(territory_service, "get_storage", lambda: storage)
    monkeypatch.setattr(territory_repo, "find_pending_by_storage_key", found)
    monkeypatch.setattr(territory_repo, "get_owned", found)
    monkeypatch.setattr(Path, "open", paused_open)
    # Both requests share one event loop. Disk I/O must leave this loop free.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        uploading = asyncio.create_task(
            http.put(
                f"/app/territory/attempts/_bridge/upload/{key}",
                content=photo,
                headers={"Content-Type": "image/jpeg"},
            )
        )
        try:
            assert await asyncio.to_thread(started.wait, 5)
            assert not uploading.done()
            response = await http.post(f"/app/territory/attempts/{attempt.id}/confirm")
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "photo_not_uploaded"
            assert attempt.status == "PENDING_UPLOAD"
            assert client.fake_session.commits == 0
            assert client.published_vision_attempts == []
        finally:
            resume.set()
            uploaded = await uploading
        assert uploaded.status_code == 200
        response = await http.post(f"/app/territory/attempts/{attempt.id}/confirm")
        assert response.status_code == 200
        assert response.json()["status"] == "VISION_PENDING"
        assert attempt.photo_object_generation == sha256(photo).hexdigest()
        assert attempt.photo_size_bytes == len(photo)
        assert client.published_vision_attempts == [attempt.id]


async def test_http_site_lookup_uses_server_coordinates(monkeypatch):
    import httpx

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={"sites": [{"site_id": SITE_ID, "lat": 37.5, "lng": 127.0, "distance_m": 3.0}]},
        )

    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=transport, **kwargs),
    )
    lookup = HttpTerritorySiteLookup("http://place-search:8000", timeout_seconds=2)
    site = await lookup.find_near_capture(
        site_id=SITE_ID,
        lat=Decimal("37.50001"),
        lng=Decimal("127.00001"),
    )
    assert site == TerritorySiteSnapshot(SITE_ID, Decimal("37.5"), Decimal("127.0"))
    assert seen["radius_m"] == "50"
    assert seen["limit"] == "100"


async def test_http_site_lookup_fails_closed_on_broken_contract(monkeypatch):
    import httpx

    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=transport, **kwargs),
    )
    lookup = HttpTerritorySiteLookup("http://place-search:8000", timeout_seconds=2)
    with pytest.raises(TerritorySiteUnavailableError):
        await lookup.find_near_capture(
            site_id=SITE_ID,
            lat=Decimal("37.5"),
            lng=Decimal("127.0"),
        )


def test_init_and_migration_share_the_visit_invariants():
    init_sql = (REPO / "db/init/08_territory_visits.sql").read_text(encoding="utf-8")
    migration_sql = (REPO / "db/migrations/2026-09-03_territory_visits.sql").read_text(
        encoding="utf-8"
    )
    verify_sql = (REPO / "db/migrations/verify_2026-09-03_territory_visits.sql").read_text(
        encoding="utf-8"
    )
    required = (
        "CREATE TABLE IF NOT EXISTS territory_attempts",
        "CREATE TABLE IF NOT EXISTS territory_verified_visits",
        "territory_attempts_owner_capture_unique",
        "territory_attempts_distance_range",
        "territory_attempts_location_evidence",
        "territory_attempts_confirmed_photo_identity",
        "territory_attempts_final_vision_metadata",
    )
    assert all(token in init_sql for token in required)
    assert all(token in migration_sql for token in required)
    assert "fact_for_nonverified_attempt" in verify_sql
    assert "terminal_photo_cleanup_pending" in verify_sql
    assert "confirmed_without_photo_identity" in verify_sql
