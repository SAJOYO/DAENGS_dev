"""실제 라우터·서비스·직렬화를 fake 저장소로 연결한다."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session, get_snapshot_session
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.routers import walk_entry as router
from daengs_backend.services.walk_records.v1 import repo

OWNER, PET, WALK, ENTRY = [uuid.uuid4() for _ in range(4)]
START = datetime(2026, 9, 5, tzinfo=UTC)


@pytest.fixture
def client(monkeypatch):
    rows = {}
    walk = SimpleNamespace(
        id=WALK, pet_ids=[PET], started_at=START, ended_at=START + timedelta(hours=1)
    )
    session = SimpleNamespace(add=lambda row: rows.update({row.id: row}), commit=AsyncMock())
    monkeypatch.setattr(
        repo, "owned_walk", AsyncMock(side_effect=lambda s, o, w, **kw: walk if w == WALK else None)
    )
    monkeypatch.setattr(repo, "get_entry", AsyncMock(side_effect=lambda s, w, e: rows.get(e)))
    monkeypatch.setattr(repo, "entries", AsyncMock(side_effect=lambda s, ws: list(rows.values())))
    monkeypatch.setattr(repo, "pet_is_accessible", AsyncMock(side_effect=lambda s, o, p: p == PET))
    monkeypatch.setattr(repo, "profile_walks", AsyncMock(return_value=[walk]))
    # 연결 안 된 아이는 자기 하나가 그룹입니다 (MVP 결정 §7).
    monkeypatch.setattr(repo, "pet_group_ids", AsyncMock(return_value=[PET]))
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: AppPrincipal(
        app_user_id=OWNER
    )
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_snapshot_session] = lambda: session
    return TestClient(app)


def body():
    return {
        "expected_revision": 0,
        "mutation_id": str(uuid.uuid4()),
        "content": {
            "kind": "behavior",
            "behavior_code": "sniffing",
            "pet_id": str(PET),
            "recorded_at": START.isoformat(),
            "location": {
                "lat": 37.5,
                "lng": 127,
                "captured_at": START.isoformat(),
                "accuracy_m": 5,
            },
        },
    }


def test_crud_profile_retry_and_deletion(client):
    path = f"/app/walks/{WALK}/entries/{ENTRY}"
    request = body()
    response = client.put(path, json=request)
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1
    assert client.put(path, json=request).json()["revision"] == 1
    profile = client.post("/app/walks/record-profile/query", json={"pet_id": str(PET)})
    assert profile.status_code == 200, profile.text
    assert profile.json()["behaviors"]["sniffing"]["entry_count"] == 1
    assert profile.json()["evidence"][0]["context_status"] == "not_requested"
    deleted = client.delete(path, params={"expected_revision": 1, "mutation_id": str(uuid.uuid4())})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["content"] is None
    assert client.put(path, json=body()).status_code == 409
    profile = client.post("/app/walks/record-profile/query", json={"pet_id": str(PET)}).json()
    assert profile["walk_count"] == 1 and profile["evidence"] == []


def test_not_owned_walk_and_pet_are_not_found(client):
    assert client.get(f"/app/walks/{uuid.uuid4()}/entries").status_code == 404
    assert (
        client.post(
            "/app/walks/record-profile/query", json={"pet_id": str(uuid.uuid4())}
        ).status_code
        == 404
    )


def test_no_token_is_unauthorized():
    app = FastAPI()
    app.include_router(router.router)
    with TestClient(app) as client:
        assert client.get(f"/app/walks/{WALK}/entries").status_code == 401


def test_context_read_keeps_record_owner_and_tombstone_boundary(client):
    path = f"/app/walks/{WALK}/entries/{ENTRY}"
    assert client.get(path + "/contexts").status_code == 404
    assert client.put(path, json=body()).status_code == 200
    context = client.get(path + "/contexts")
    assert context.status_code == 200
    assert context.json() == {
        "entry_id": str(ENTRY),
        "revision": 1,
        "status": "disabled",
        "sources": [],
    }
    assert client.get(f"/app/walks/{uuid.uuid4()}/entries/{ENTRY}/contexts").status_code == 404
    client.delete(path, params={"expected_revision": 1, "mutation_id": str(uuid.uuid4())})
    assert client.get(path + "/contexts").status_code == 404


def test_enabled_writer_reserves_before_commit_without_provider_io(client, monkeypatch):
    from daengs_backend.config import settings
    from daengs_backend.repositories import walk_entry_context

    monkeypatch.setattr(settings, "walk_entry_context_enabled", True)
    reserve = AsyncMock()
    monkeypatch.setattr(walk_entry_context, "enqueue", reserve)
    request = body()
    request["content"] = {"kind": "note", "note": "위치 없는 글", "recorded_at": START.isoformat()}
    response = client.put(f"/app/walks/{WALK}/entries/{ENTRY}", json=request)
    assert response.status_code == 200
    reserve.assert_awaited_once()
    assert reserve.call_args.args[1].payload["note"] == "위치 없는 글"
