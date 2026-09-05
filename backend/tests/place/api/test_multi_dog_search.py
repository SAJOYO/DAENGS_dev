"""HTTP-to-evaluation contract without PostGIS; candidate retrieval is replaced once."""

from itertools import permutations
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from daengs_place.core.db import get_session
from daengs_place.main import app
from daengs_place.place import search as service
from daengs_place.place.contracts import PlaceResult, RestrictionChip, RestrictionFacts
from daengs_place.place.restriction_projection import project


@pytest.fixture
def client(monkeypatch):
    async def no_db():
        yield None

    async def candidates(db, plan):
        key = {"source": "test", "ref": "one"}
        place = PlaceResult.model_validate(
            {
                "key": key,
                "name": "Example",
                "lat": 37.5,
                "lng": 127,
                "distance_m": 1,
                "match": {"source": key, "kind": "cafe"},
                "classifications": [
                    {
                        "source": key,
                        "kind": "cafe",
                        "source_category": "cafe",
                        "mapping_version": "test",
                    }
                ],
                "facts": {
                    "pet_access": {"allowed": True, "max_kg": 10},
                    "restrictions": {
                        "state": "restricted",
                        "parse_state": "mapped",
                        "raw": "two dogs per room",
                        "chips": [
                            {
                                "code": "limit:max_dogs",
                                "label": "count limit",
                                "params": {"max": "2"},
                            }
                        ],
                    },
                },
            }
        )
        return service.PlaceSearchResponse(
            conditions=plan.conditions,
            groups=[
                service.PlaceSearchGroup(
                    kind="cafe", limit=50, results=[service._hit(place, plan.conditions)]
                )
            ],
        )

    mock = AsyncMock(side_effect=candidates)
    monkeypatch.setattr(service, "search_place_plan", mock)
    app.dependency_overrides[get_session] = no_db
    try:
        with TestClient(app) as http:
            yield http, mock
    finally:
        app.dependency_overrides.pop(get_session, None)


def request(**extra):
    return {"lat": 37.5, "lng": 127, "kinds": ["cafe"], **extra}


def test_per_dog_echo_and_unknown_profiles_preserve_raw_without_filtering(client):
    http, query = client
    dogs = [
        {"ref": "a", "revision": "v1", "dog_weight_kg": 9},
        {"ref": "b", "dog_weight_kg": 11},
        {"ref": "c"},
    ]
    response = http.post("/v2/places/search", json=request(dogs=dogs))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [d["ref"] for d in body["dogs"]] == ["a", "b", "c"]
    assert body["dogs"][0]["revision"] == "v1"
    assert body["evaluated_at"]
    hits = body["groups"][0]["results"]
    assert len(hits) == 1
    evaluations = hits[0]["evaluations"]["dogs"]
    assert [d["dog_access"]["state"] for d in evaluations] == [
        "compatible",
        "incompatible",
        "unknown",
    ]
    assert all(d["restrictions"]["state"] == "unknown" for d in evaluations)
    assert hits[0]["place"]["facts"]["restrictions"]["raw"] == "two dogs per room"
    assert "dog_access" not in hits[0]["evaluations"]
    query.assert_awaited_once()


@pytest.mark.parametrize(
    "extra",
    [
        {"dogs": [{"ref": "a"}, {"ref": "a"}]},
        {"dogs": [{"ref": "a"}], "conditions": {"dog_weight_kg": 9}},
        {"dogs": [{"ref": "a", "dog_weigth_kg": 9}]},
        {"dogs": [{"ref": str(i)} for i in range(21)]},
        {"dogs": [{"ref": "a", "dog_weight_kg": 0}]},
    ],
)
def test_invalid_profiles_rejected_before_candidates(client, extra):
    http, query = client
    assert http.post("/v2/places/search", json=request(**extra)).status_code == 422
    query.assert_not_awaited()


def test_single_condition_contract_is_unchanged(client):
    http, _ = client
    body = http.post("/v2/places/search", json=request(conditions={"dog_weight_kg": 9})).json()
    assert "dogs" not in body
    assert body["groups"][0]["results"][0]["evaluations"]["dog_access"]["state"] == "compatible"


def test_confirmed_blocker_wins_regardless_of_unknown_condition_order():
    chips = [
        RestrictionChip(code="deny:size", label="large", applies_to="size:large"),
        RestrictionChip(code="deny:species_dog", label="dogs"),
    ]
    for ordered in permutations(chips):
        result = project(
            RestrictionFacts(state="restricted", chips=list(ordered)),
            dog_size=None,
            dog_age_years=None,
        )
        assert result.state == "incompatible"
        assert result.reason == "species_denied"


def test_medical_per_dog_evaluation_is_explicitly_unavailable():
    key = {"source": "test", "ref": "hospital"}
    place = PlaceResult.model_validate(
        {
            "key": key,
            "name": "Hospital",
            "lat": 37.5,
            "lng": 127,
            "distance_m": 1,
            "match": {"source": key, "kind": "hospital"},
            "classifications": [
                {
                    "source": key,
                    "kind": "hospital",
                    "source_category": "hospital",
                    "mapping_version": "test",
                }
            ],
            "facts": {},
        }
    )
    result = service._hit(place, service.PlaceDogSnapshot(ref="a"))
    assert result.evaluations.dog_access is None
    assert result.evaluations.restrictions is None
