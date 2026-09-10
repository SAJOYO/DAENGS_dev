"""Public renewal contract: request validation, principal forwarding, stable error codes."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, current_app_user
from daengs_backend.main import app
from daengs_backend.schemas.territory_claim import RenewalResponse
from daengs_backend.services import territory_renewal
from daengs_backend.services.territory_ownership import ClaimConflict, ClaimNotFound
from daengs_backend.services.territory_site_lookup import get_territory_site_lookup
from tests.territory.support import ownership as base


@pytest.fixture
def client():
    owner = uuid.uuid4()
    app.dependency_overrides[current_app_user] = lambda: AppPrincipal(app_user_id=owner)
    app.dependency_overrides[get_session] = lambda: object()
    app.dependency_overrides[get_territory_site_lookup] = lambda: base.Lookup()
    with TestClient(app) as http:
        yield http, owner
    app.dependency_overrides.clear()


async def test_renewal_auth_identity_and_serialization(client, monkeypatch):
    http, owner = client
    claim, renewal = uuid.uuid4(), uuid.uuid4()
    expires = datetime.now(UTC) + timedelta(hours=72)

    async def success(db, supplied_owner, claim_id, renewal_id, body, lookup):
        assert supplied_owner == owner and claim_id == claim and renewal_id == renewal
        assert body.expected_site_version == 7
        return RenewalResponse(renewal_id=renewal, site_version=8, expires_at=expires)

    monkeypatch.setattr(territory_renewal, "renew", success)
    body = base.mark_body(uuid.uuid4(), uuid.uuid4()).model_dump(mode="json")
    path = f"/app/territory/claims/{claim}/renewals/{renewal}"
    assert http.put(path, json=body).status_code == 422
    body["expected_site_version"] = 7
    response = http.put(path, json=body)
    assert response.status_code == 200
    assert response.json() == RenewalResponse(
        renewal_id=renewal, site_version=8, expires_at=expires
    ).model_dump(mode="json")
    assert http.put(path, json=body | {"owner_id": str(owner)}).status_code == 422

    async def missing(*args):
        raise ClaimNotFound

    monkeypatch.setattr(territory_renewal, "renew", missing)
    assert http.put(path, json=body).status_code == 404

    async def changed(*args):
        raise ClaimConflict("site_changed")

    monkeypatch.setattr(territory_renewal, "renew", changed)
    rejected = http.put(path, json=body)
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": {"code": "site_changed"}}
