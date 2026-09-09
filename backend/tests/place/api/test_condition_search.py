"""Public filter wire contract; engine/SQL semantics have their own PostGIS tests."""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from daengs_place.api import places_v3
from daengs_place.core.db import get_session
from daengs_place.main import app
from daengs_place.place.filters.contract import FilterState
from daengs_place.place.filters.service import FilterGroup, FilterResponse


def test_cross_platform_fixture_matches_the_public_schema(client):
    fixture = json.loads(
        Path(__file__).with_name("condition_wire.json").read_text(encoding="utf-8")
    )
    request = places_v3.FilterSearchRequest.model_validate(fixture["request"])
    response = places_v3.FilterSearchResponse.model_validate(fixture["response"])
    assert response.applied_state == request.state
    assert response.model_dump(mode="json") == fixture["response"]
    assert client[0].get("/v3/places/capabilities").json() == fixture["capabilities"]


def request():
    return {
        "revision": 7,
        "search_request_id": "manual-7",
        "state": {
            "candidate_kinds": ["cafe", "restaurant"],
            "spatial": {"lat": 37.5, "lng": 127, "radius_m": 3000},
            "hard": {
                "all": [
                    {
                        "id": "parking",
                        "capability": "operations.parking",
                        "op": "eq",
                        "value": False,
                    }
                ]
            },
            "unknown_policy": "separate",
            "result_policy": {"limit_per_kind": 50, "uncertain_limit_per_kind": 20},
        },
    }


@pytest.fixture
def client(monkeypatch):
    async def no_db():
        yield None

    async def execute(db, state):
        return FilterResponse(
            applied_state=state,
            evaluated_at=datetime.now(UTC),
            groups=tuple(FilterGroup(kind=k) for k in state.candidate_kinds),
        )

    query = AsyncMock(side_effect=execute)
    monkeypatch.setattr(places_v3, "search_filtered_places", query)
    app.dependency_overrides[get_session] = no_db
    try:
        with TestClient(app) as http:
            yield http, query
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_capabilities_are_versioned_and_do_not_expose_sql(client):
    http, query = client
    body = http.get("/v3/places/capabilities").json()
    assert body["contract_version"] == "place-filter-v1"
    assert body["max_candidate_kinds"] == 6
    assert len(body["capabilities"]) == 3
    assert body["capabilities"][1]["always_unknown_kinds"] == ["hospital", "pharmacy"]
    assert "sql_column" not in str(body)
    query.assert_not_awaited()


def test_full_normalized_state_and_request_identity_round_trip(client):
    http, query = client
    sent = request()
    result = http.post("/v3/places/search", json=sent)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["revision"] == sent["revision"]
    assert body["search_request_id"] == sent["search_request_id"]
    assert body["applied_state"] == FilterState.model_validate(sent["state"]).model_dump(
        mode="json"
    )
    assert body["execution_status"] == "complete"
    assert all(g["total"] is None for g in body["groups"])
    # Stateless endpoint deliberately does not claim cross-client revision locking.
    assert http.post("/v3/places/search", json=sent).status_code == 200
    assert query.await_count == 2


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(revision=True),
        lambda r: r.update(search_request_id=""),
        lambda r: r["state"].update(contract_version="future"),
        lambda r: r["state"]["hard"]["all"][0].update(value="false"),
        lambda r: r["state"]["hard"]["all"][0].update(capability="vibe.quiet"),
        lambda r: r["state"]["hard"]["all"].append(
            {"id": "yes", "capability": "operations.parking", "op": "eq", "value": True}
        ),
        lambda r: r["state"].update(locked=True),
    ],
)
def test_invalid_state_never_executes_partially(client, change):
    http, query = client
    sent = request()
    change(sent)
    assert http.post("/v3/places/search", json=sent).status_code == 422
    query.assert_not_awaited()


@pytest.mark.parametrize("failure", [SQLAlchemyError("private DB error"), TimeoutError()])
def test_failure_is_not_an_empty_success(client, failure):
    http, query = client
    query.side_effect = failure
    result = http.post("/v3/places/search", json=request())
    assert result.status_code == 503
    assert result.json() == {"detail": {"code": "filter_search_unavailable"}}
