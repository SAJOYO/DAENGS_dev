import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.routers import facility_discovery as facility_router
from daengs_backend.routers.facility_discovery import router
from daengs_backend.schemas.facility_discovery import (
    FacilityActionRequest,
    FacilityDiscoveryRequest,
)
from daengs_backend.services.facility_discovery import (
    FacilityDiscoveryError,
    FacilityDiscoveryService,
    get_facility_discovery_service,
)
from daengs_place.api import facility_internal
from daengs_place.core.db import get_session
from daengs_place.main import app as place_app
from tests.facility_session_store import MemorySessions
from tests.place.place.discovery.test_facility import make_service, request


@pytest.fixture(autouse=True)
def active_account(monkeypatch):
    async def check(user_id):
        return None

    monkeypatch.setattr(facility_router, "require_active_facility_owner", check)


@pytest.fixture
def connected():
    service, calls = make_service()

    async def db():
        yield None

    place_app.dependency_overrides[get_session] = db
    original = facility_internal.get_place_discovery_service
    facility_internal.get_place_discovery_service = lambda: service
    upstream = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=place_app), base_url="http://place"
    )
    gateway = FacilityDiscoveryService(upstream, store=MemorySessions())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_facility_discovery_service] = lambda: gateway
    try:
        with TestClient(app) as client:
            yield client, calls
    finally:
        place_app.dependency_overrides.clear()
        facility_internal.get_place_discovery_service = original
        asyncio.run(upstream.aclose())


def test_authenticated_gateway_to_place_returns_context_and_real_core_result(connected):
    client, calls = connected
    body = request(dogs=[{"ref": "review", "dog_weight_kg": 10}]).model_dump(mode="json")
    token = create_access_token(uuid4(), SubjectType.APP)
    response = client.post(
        "/app/places/discovery", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["request"] == body
    assert result["lenses"][0]["search"]["dogs"] == body["dogs"]
    assert [c[0] for c in calls] == ["llm", "db"]
    assert "confirmation_context" not in response.text and '"trace"' not in response.text


def test_no_token_never_calls_provider(connected):
    client, calls = connected
    response = client.post("/app/places/discovery", json=request().model_dump(mode="json"))
    assert response.status_code == 401 and not calls


def test_followup_owns_state_is_idempotent_and_rejects_stale_or_injected_plans(connected):
    client, calls = connected
    headers = {"Authorization": f"Bearer {create_access_token(uuid4(), SubjectType.APP)}"}
    first = client.post(
        "/app/places/discovery", json=request().model_dump(mode="json"), headers=headers
    ).json()
    action = {
        "client_request_id": str(uuid4()),
        "search_id": first["search_id"],
        "expected_revision": 1,
        "action": {"type": "confirm", "lens_id": first["lenses"][0]["id"]},
    }
    foreign = {"Authorization": f"Bearer {create_access_token(uuid4(), SubjectType.APP)}"}
    assert (
        client.post("/app/places/discovery/actions", json=action, headers=foreign).status_code
        == 410
    )
    assert client.post("/app/places/discovery/actions", json=action).status_code == 401
    assert (
        client.post(
            "/app/places/discovery/actions", json=action | {"continuation": {}}, headers=headers
        ).status_code
        == 422
    )
    assert len(calls) == 2
    response = client.post("/app/places/discovery/actions", json=action, headers=headers)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["revision"] == 2 and updated["expires_at"] == first["expires_at"]
    assert updated["action_request"] == action and updated["request"] == first["request"]
    assert "continuation" not in updated and "confirmation_context" not in response.text
    duplicate = client.post("/app/places/discovery/actions", json=action, headers=headers)
    assert duplicate.json() == updated and len(calls) == 3
    stale = client.post(
        "/app/places/discovery/actions",
        json=action | {"client_request_id": str(uuid4())},
        headers=headers,
    )
    assert stale.status_code == 409 and len(calls) == 3


def test_expired_state_never_calls_place(connected):
    client, calls = connected
    headers = {"Authorization": f"Bearer {create_access_token(uuid4(), SubjectType.APP)}"}
    first = client.post(
        "/app/places/discovery", json=request().model_dump(mode="json"), headers=headers
    ).json()
    gateway = client.app.dependency_overrides[get_facility_discovery_service]()
    saved = json.loads(gateway.store().items[first["search_id"]])
    saved["expires"] = 0
    gateway.store().items[first["search_id"]] = json.dumps(saved)
    action = {
        "client_request_id": str(uuid4()),
        "search_id": first["search_id"],
        "expected_revision": 1,
        "action": {"type": "confirm", "lens_id": first["lenses"][0]["id"]},
    }
    response = client.post("/app/places/discovery/actions", json=action, headers=headers)
    assert response.status_code == 410 and len(calls) == 2


def test_inactive_account_never_starts_search(connected, monkeypatch):
    async def inactive(user_id):
        raise FacilityDiscoveryError("facility_login_required")

    monkeypatch.setattr(facility_router, "require_active_facility_owner", inactive)
    client, calls = connected
    response = client.post(
        "/app/places/discovery",
        json=request().model_dump(mode="json"),
        headers={"Authorization": f"Bearer {create_access_token(uuid4(), SubjectType.APP)}"},
    )
    assert response.status_code == 401 and not calls


async def test_two_workers_cannot_commit_two_actions_at_the_same_revision():
    from daengs_place.place.discovery.facility import FacilityDiscoveryRequest as PlaceRequest
    from daengs_place.place.discovery.facility import (
        FacilityInternalAction,
        continue_facilities,
        start_facilities,
    )

    engine, calls = make_service()
    reached = 0
    both = asyncio.Event()

    async def transport(req):
        nonlocal reached
        payload = json.loads(req.content)
        if req.url.path.endswith("/actions"):
            reached += 1
            if reached == 2:
                both.set()
            await both.wait()
            result = await continue_facilities(
                None, FacilityInternalAction.model_validate(payload), engine
            )
        else:
            result = await start_facilities(None, PlaceRequest.model_validate(payload), engine)
        return httpx.Response(200, json=result.model_dump(mode="json"))

    store = MemorySessions()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        workers = [
            FacilityDiscoveryService(client, store=store, base_url="http://place", timeout=5)
            for _ in range(2)
        ]
        first = await workers[0].search(
            FacilityDiscoveryRequest.model_validate(request().model_dump()), "owner"
        )

        def action():
            return FacilityActionRequest(
                client_request_id=uuid4(),
                search_id=first.search_id,
                expected_revision=1,
                action={"type": "confirm", "lens_id": first.lenses[0].id},
            )

        outcomes = await asyncio.gather(
            *(worker.act(action(), "owner") for worker in workers), return_exceptions=True
        )
    assert (
        sum(
            isinstance(item, FacilityDiscoveryError) and item.code == "facility_conflict"
            for item in outcomes
        )
        == 1
    )
    assert json.loads(store.items[str(first.search_id)])["result"]["revision"] == 2
    assert [c[0] for c in calls].count("llm") == 1


@pytest.mark.parametrize("status,expected", [(503, 503), (504, 504), (500, 502), (200, 502)])
async def test_upstream_failures_are_sanitized(status, expected):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(status, text="secret provider response"),
        )
    ) as client:
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_facility_discovery_service] = lambda: FacilityDiscoveryService(
            client, store=MemorySessions()
        )
        with TestClient(app) as caller:
            response = caller.post(
                "/app/places/discovery",
                json=request().model_dump(mode="json"),
                headers={
                    "Authorization": f"Bearer {create_access_token(uuid4(), SubjectType.APP)}"
                },
            )
        assert response.status_code == expected
        assert "secret" not in response.text


async def test_valid_response_for_a_different_radius_is_rejected():
    req = FacilityDiscoveryRequest.model_validate(request().model_dump(mode="json"))
    echo = req.model_dump(mode="json")
    echo["spatial"]["radius_m"] = 3000
    response = {
        "contract_version": "facility-discovery-v1",
        "search_id": str(uuid4()),
        "request": echo,
        "outcome": "empty",
        "lenses": [],
        "signals": [],
        "notices": [],
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))
    ) as client:
        with pytest.raises(FacilityDiscoveryError, match="facility_invalid_response"):
            await FacilityDiscoveryService(client, store=MemorySessions()).search(req, "owner")
