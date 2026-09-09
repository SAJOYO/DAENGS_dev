"""v5 crosses the real API/scene boundary without treating a pin as raw GPS."""

import copy
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from daengs_backend.config import settings
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.repositories import walk_entry_v2 as pins
from daengs_backend.services.walk_storyboard_context import lookup_contexts
from daengs_backend.services.walk_storyboard_titles import title_storyboard
from daengs_walk.storyboard import StoryboardBundleV5
from tests.walk import test_walk_storyboard as storyboard_tests
from tests.walk.test_walk_storyboard import ENTRY, PATH, START, router

V5 = "walk-storyboard-candidates-v5"
live = storyboard_tests.live


@pytest.fixture
def pin_live(live, monkeypatch):
    client, state, lookup = live
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", True)
    at = (START + timedelta(seconds=20)).isoformat()
    pin = {
        "resolution_id": str(uuid.uuid4()),
        "state": "resolved",
        "method": "estimated",
        "target_at": at,
        "point": {"lat": 37.6, "lng": 127.2},
        "uncertainty_m": None,
        "uncertainty_basis": "unknown",
    }
    row = WalkEntry(
        id=ENTRY,
        walk_id=state.walk.id,
        revision=1,
        mutation_id=uuid.uuid4(),
        payload={
            "kind": "behavior",
            "behavior_code": "sniffing",
            "recorded_at": at,
            "location": None,
        },
    )
    state.entries = [row]
    sidecar = SimpleNamespace(entry_id=ENTRY, pin_revision=1, payload=pin)
    monkeypatch.setattr(pins, "pins", AsyncMock(return_value=[sidecar]))
    monkeypatch.setattr(pins, "contains_v2", AsyncMock(return_value=True))
    client.app.dependency_overrides[router.get_title_generator] = lambda: AsyncMock(
        side_effect=lambda b: b
    )
    return client, state, lookup, pin, sidecar


def test_v5_keeps_estimated_pin_entry_time_and_no_observation_or_route(pin_live):
    client, state, lookup, pin, _ = pin_live
    before = copy.deepcopy(state.entries[0].payload)
    response = client.post(PATH, json={"expected_entries": {str(ENTRY): 1}, "bundle_format": V5})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["status"] == "ready", value
    bundle = value["bundle"]
    scene = next(s for s in bundle["scenes"] if s["entry"])
    assert scene["observation"] is None and scene["route"] is None
    assert scene["pin"]["method"] == "estimated" and scene["pin"]["point"] == pin["point"]
    assert scene["started_at"] == scene["pin"]["target_at"]
    assert state.entries[0].payload == before
    assert lookup.call_args.args[0]["entry_anchors"][0]["location"] == pin["point"]
    assert client.get(PATH).status_code == 409  # No silent v1 reinterpretation.


def test_unlocated_and_deleted_entries_do_not_make_entry_queries(pin_live):
    client, state, lookup, pin, _ = pin_live
    pin.update(state="unlocated", method="none", point=None)
    state.entries.append(
        WalkEntry(
            id=uuid.uuid4(),
            walk_id=state.walk.id,
            revision=2,
            mutation_id=uuid.uuid4(),
            payload=None,
        )
    )
    result = client.post(
        PATH,
        json={
            "expected_entries": {str(e.id): e.revision for e in state.entries},
            "bundle_format": V5,
        },
    ).json()
    assert result["status"] == "ready", result
    scenes = [s for s in result["bundle"]["scenes"] if s["entry"]]
    assert len(scenes) == 1 and scenes[0]["pin"]["state"] == "unlocated"
    assert lookup.call_args.args[0]["entry_anchors"] == []


def test_provisional_rejected_and_changed_pin_invalidates_inflight_generation(pin_live):
    client, state, lookup, pin, sidecar = pin_live
    pin["state"] = "provisional"
    request = {"expected_entries": {str(ENTRY): 1}, "bundle_format": V5}
    assert client.post(PATH, json=request).status_code == 409
    lookup.assert_not_awaited()
    pin["state"] = "resolved"

    async def changed(selection):
        state.entries[0].revision = 2
        sidecar.pin_revision = 2
        pin["point"] = {"lat": 37.7, "lng": 127.3}
        return {}

    lookup.side_effect = changed
    result = client.post(PATH, json=request).json()
    assert result["status"] == "stale" and result["bundle"] is None


async def test_place_queries_use_pin_basis_and_titles_preserve_v5(pin_live):
    client, _, _, pin, _ = pin_live
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "groups": [
                    {
                        "kind": "cafe",
                        "results": [
                            {
                                "place": {
                                    "name": "카페",
                                    "distance_m": 25,
                                    "key": {"source": "public", "ref": "1"},
                                }
                            }
                        ],
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        contexts = await lookup_contexts(
            {
                "anchors": [],
                "entry_anchors": [
                    {"id": "entry:x", "location": pin["point"], "location_basis": "estimated"}
                ],
            },
            client=http,
        )
    assert requests[0]["lat"] == pin["point"]["lat"]
    assert contexts["entry:x"]["facts"][0].startswith("추정 위치 기준")
    value = client.post(
        PATH, json={"expected_entries": {str(ENTRY): 1}, "bundle_format": V5}
    ).json()
    bundle = StoryboardBundleV5.model_validate(value["bundle"])
    titled = await title_storyboard(bundle, generate=AsyncMock(side_effect=ValueError("offline")))
    assert titled.format == V5 and titled.scenes == bundle.scenes


async def test_route_and_pin_lookups_have_independent_eight_request_budgets():
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"groups": []})

    selection = {
        key: [{"id": f"{key}:{i}", "location": {"lat": 37.5, "lng": 127}} for i in range(12)]
        for key in ("anchors", "entry_anchors")
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await lookup_contexts(selection, client=http)
    assert len(requests) == len(result) == 16
    assert "anchors:8" not in result and "entry_anchors:8" not in result
