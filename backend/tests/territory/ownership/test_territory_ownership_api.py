"""HTTP validation/authentication without external DB or broker."""

import uuid

import pytest
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, current_app_user
from daengs_backend.main import app
from daengs_backend.services import territory_ownership as service

PREFIX = "/app/territory"
SITE = "territory-site:hex-v1:140:324:777"


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("put", f"/claim-sessions/{uuid.uuid4()}", {}),
        ("patch", f"/claim-sessions/{uuid.uuid4()}", {}),
        ("get", f"/claim-sessions/{uuid.uuid4()}", None),
        ("get", f"/occupancies?site_ids={SITE}", None),
        ("post", "/claims", {}),
        ("get", f"/claims/{uuid.uuid4()}", None),
        ("put", f"/claims/{uuid.uuid4()}/photos/{uuid.uuid4()}", None),
    ],
)
def test_claim_endpoints_require_app_auth(method, path, body):
    with TestClient(app) as client:
        response = client.request(method, PREFIX + path, json=body)
    assert response.status_code == 401


@pytest.fixture
def client():
    app.dependency_overrides[current_app_user] = lambda: AppPrincipal(app_user_id=uuid.uuid4())
    app.dependency_overrides[get_session] = lambda: object()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_shared_query_is_bounded_and_site_ids_validated(client):
    for params in ([], [("site_ids", "bad")], [("site_ids", SITE)] * 101):
        assert client.get(PREFIX + "/occupancies", params=params).status_code == 422


def test_session_rejects_duplicate_dogs_naive_time_and_client_ownership(client):
    pet = str(uuid.uuid4())
    body = {"started_at": "2026-09-05T00:00:00Z", "pet_ids": [pet]}
    for override in (
        {"pet_ids": [pet, pet]},
        {"started_at": "2026-09-05T00:00:00"},
        {"owner_id": str(uuid.uuid4())},
    ):
        assert (
            client.put(PREFIX + f"/claim-sessions/{uuid.uuid4()}", json=body | override).status_code
            == 422
        )


def test_conflicts_return_stable_codes_and_missing_owned_objects_404(client, monkeypatch):
    async def changed(*args):
        raise service.ClaimConflict("session_changed")

    monkeypatch.setattr(service, "change_phase", changed)
    response = client.patch(
        PREFIX + f"/claim-sessions/{uuid.uuid4()}", json={"phase": "ENDED", "expected_version": 0}
    )
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "session_changed"}}

    async def missing(*args):
        raise service.ClaimNotFound

    monkeypatch.setattr(service, "get_claim", missing)
    assert client.get(PREFIX + f"/claims/{uuid.uuid4()}").status_code == 404
