import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.routers import facility_discovery
from daengs_backend.routers.place_filter_edits import router
from daengs_backend.schemas.place_filter_edits import (
    FilterEditAction,
    FilterEditRequest,
    FilterEditResponse,
)
from daengs_backend.services.facility_discovery import FacilityDiscoveryError
from daengs_backend.services.place_filter_edits import FilterEditService, get_filter_edit_service
from daengs_place.api import filter_edits_internal as internal
from daengs_place.core.db import get_session
from daengs_place.main import app as place_app
from daengs_place.place.filters.service import FilterGroup, FilterResponse
from tests.facility_session_store import MemorySessions
from tests.place.place.filters.test_edits import atom, base, edit, proposal


def test_android_fixture_is_actual_python_compiler_and_response_contract():
    from daengs_place.api.places_v3 import FilterSearchResponse
    from daengs_place.place.filters.contract import FilterState
    from daengs_place.place.filters.edits import EditProposal, compile_edits

    fixture = json.loads(
        (Path(__file__).parent / "place/api/filter_edit_wire.json").read_text(encoding="utf-8")
    )
    first = FilterEditResponse.model_validate(fixture["proposal_response"])
    done = FilterEditResponse.model_validate(fixture["applied_response"])
    compiled = compile_edits(
        FilterState.model_validate(first.request.base_state),
        first.request.query,
        EditProposal.model_validate(first.compiled.proposal),
    )
    assert compiled.model_dump(mode="json") == fixture["proposal_response"]["compiled"]
    assert first.request.model_dump(mode="json") == fixture["request"]
    assert done.action_request.model_dump(mode="json") == fixture["action"]
    assert done.model_dump(mode="json") == fixture["applied_response"]
    assert FilterSearchResponse.model_validate(done.result).applied_state == compiled.proposed_state


@pytest.fixture
def connected(monkeypatch):
    proposer = AsyncMock()
    proposer.propose.return_value = proposal(edit("주차 가능한 곳", atom=atom()))

    async def execute(db, state):
        return FilterResponse(
            applied_state=state,
            evaluated_at=datetime.now(UTC),
            groups=tuple(FilterGroup(kind=k) for k in state.candidate_kinds),
        )

    query = AsyncMock(side_effect=execute)
    monkeypatch.setattr(internal, "search_filtered_places", query)
    monkeypatch.setattr(facility_discovery, "require_active_facility_owner", AsyncMock())

    async def db():
        yield None

    place_app.dependency_overrides[get_session] = db
    place_app.dependency_overrides[internal.get_proposer] = lambda: proposer
    upstream = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=place_app), base_url="http://place"
    )
    store = MemorySessions()
    gateway = FilterEditService(upstream, store=store, base_url="http://place", timeout=3)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_filter_edit_service] = lambda: gateway
    try:
        with TestClient(app) as client:
            yield client, proposer, query, gateway, store
    finally:
        place_app.dependency_overrides.clear()
        asyncio.run(upstream.aclose())


def headers():
    return {"Authorization": f"Bearer {create_access_token(uuid4(), SubjectType.APP)}"}


def request():
    return {
        "client_request_id": str(uuid4()),
        "base_revision": 7,
        "query": "주차 가능한 곳",
        "base_state": base().model_dump(mode="json"),
    }


def action(result, **changes):
    return {
        "client_request_id": str(uuid4()),
        "search_id": result["search_id"],
        "expected_revision": result["revision"],
        "current_state": result["compiled"]["base_state"],
        "confirm_changes": False,
        **changes,
    }


def test_authenticated_proposal_executes_same_engine_only_on_apply_and_replays(connected):
    client, proposer, query, _, _ = connected
    auth, body = headers(), request()
    first = client.post("/app/places/filter-edits", json=body, headers=auth)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["compiled"]["status"] == "ready"
    query.assert_not_awaited()
    sent = action(result)
    second = client.post("/app/places/filter-edits/actions", json=sent, headers=auth)
    assert second.status_code == 200, second.text
    done = second.json()
    assert done["result"]["revision"] == 8
    assert done["result"]["applied_state"] == result["compiled"]["proposed_state"]
    assert client.post("/app/places/filter-edits/actions", json=sent, headers=auth).json() == done
    assert query.await_count == 1 and proposer.propose.await_count == 1
    assert (
        client.post(
            "/app/places/filter-edits/actions", json=action(result), headers=auth
        ).status_code
        == 409
    )
    assert (
        client.post("/app/places/filter-edits/actions", json=sent, headers=headers()).status_code
        == 410
    )


def test_login_and_active_account_checked_before_provider(connected, monkeypatch):
    client, proposer, _, _, _ = connected
    assert client.post("/app/places/filter-edits", json=request()).status_code == 401
    monkeypatch.setattr(
        facility_discovery,
        "require_active_facility_owner",
        AsyncMock(side_effect=FacilityDiscoveryError("facility_login_required")),
    )
    assert (
        client.post("/app/places/filter-edits", json=request(), headers=headers()).status_code
        == 401
    )
    proposer.propose.assert_not_awaited()


def test_stale_snapshot_and_injected_plan_rejected(connected):
    client, _, query, _, _ = connected
    auth = headers()
    first = client.post("/app/places/filter-edits", json=request(), headers=auth).json()
    sent = action(first)
    sent["current_state"]["spatial"]["radius_m"] = 5000
    assert (
        client.post("/app/places/filter-edits/actions", json=sent, headers=auth).status_code == 409
    )
    sent = action(first, proposal={"edits": []})
    assert (
        client.post("/app/places/filter-edits/actions", json=sent, headers=auth).status_code == 422
    )
    query.assert_not_awaited()


def test_protected_changes_require_confirmation_but_unsupported_cannot_be_forced(connected):
    client, proposer, query, _, _ = connected
    auth, body = headers(), request()
    body["query"] = "다른 데"
    body["base_state"] = base(hard={"all": [atom()]}).model_dump(mode="json")
    proposer.propose.return_value = proposal(edit(body["query"], "remove_all", target_id="parking"))
    first = client.post("/app/places/filter-edits", json=body, headers=auth).json()
    assert (
        client.post(
            "/app/places/filter-edits/actions", json=action(first), headers=auth
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/app/places/filter-edits/actions",
            json=action(first, confirm_changes=True),
            headers=auth,
        ).status_code
        == 200
    )
    proposer.propose.return_value = proposal(
        unresolved=[
            {"evidence": edit(body["query"], atom=atom())["evidence"], "reason": "unsupported"}
        ]
    )
    first = client.post("/app/places/filter-edits", json=body, headers=auth).json()
    assert (
        client.post(
            "/app/places/filter-edits/actions",
            json=action(first, confirm_changes=True),
            headers=auth,
        ).status_code
        == 422
    )
    assert query.await_count == 1


async def test_concurrent_actions_only_publish_one_revision(connected):
    _, _, _, gateway, _ = connected
    first = await gateway.propose(FilterEditRequest.model_validate(request()), "owner")
    results = await asyncio.gather(
        *(
            gateway.apply(
                FilterEditAction.model_validate(action(first.model_dump(mode="json"))), "owner"
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert [r.code for r in results if isinstance(r, FacilityDiscoveryError)] == [
        "facility_conflict"
    ]


def test_expiry_and_database_failure_never_publish_partial_results(connected, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    client, _, query, _, store = connected
    auth = headers()
    first = client.post("/app/places/filter-edits", json=request(), headers=auth).json()
    query.side_effect = SQLAlchemyError("private details")
    response = client.post("/app/places/filter-edits/actions", json=action(first), headers=auth)
    assert response.status_code == 503 and "private details" not in response.text
    saved = json.loads(store.items[first["search_id"]])
    saved["expires"] = 0
    store.items[first["search_id"]] = json.dumps(saved)
    assert (
        client.post(
            "/app/places/filter-edits/actions", json=action(first), headers=auth
        ).status_code
        == 410
    )
