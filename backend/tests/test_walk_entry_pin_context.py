"""Pin-aware outbox retains original content and fences late provider responses."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from daengs_backend.repositories import walk_entry_v2 as pins
from daengs_backend.services import walk_entry_context as service
from daengs_backend.services import walk_entry_context_source as source
from tests import test_walk_entry_context as context_tests
from tests.test_walk_entry_context import CONTENT, body

state = context_tests.state
PIN = {
    "state": "resolved",
    "method": "estimated",
    "point": {"lat": 37.6, "lng": 127.2},
    "uncertainty_m": None,
    "uncertainty_basis": "unknown",
}


async def test_lookup_uses_pin_without_replacing_original_content():
    content = {**copy.deepcopy(CONTENT), "pin": PIN}
    before = copy.deepcopy(content)
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await source.collect("space.facility", content, client=client)
    assert requests[0]["lat"] == PIN["point"]["lat"]
    assert result.payload["location_basis"] == "estimated"
    assert result.payload["visit_confirmed"] is False
    assert content == before


@pytest.mark.parametrize("state_name,method", [("unlocated", "none"), ("provisional", "none")])
async def test_no_coordinate_does_not_borrow_original_or_call_provider(state_name, method):
    client = SimpleNamespace(post=AsyncMock())
    result = await source.collect(
        "space.facility",
        {**CONTENT, "pin": {"state": state_name, "method": method, "point": None}},
        client=client,
    )
    assert result.status == "not_requested"
    client.post.assert_not_awaited()


@pytest.mark.parametrize("change", [None, "revision", "delete"])
async def test_v2_worker_keeps_both_sources_and_discards_stale_reply(state, monkeypatch, change):
    state.job.policy_version = service.repo.PIN_POLICY
    monkeypatch.setattr(
        pins, "pin", AsyncMock(return_value=SimpleNamespace(payload=PIN, pin_revision=2))
    )

    async def collector(tag, content):
        assert not state.active[0]
        assert content["location"] == CONTENT["location"] and content["pin"] == PIN
        if change == "revision":
            state.record.revision += 1
        elif change == "delete":
            state.record.payload = None
        return source.Collected("empty", payload={"items": []})

    assert await service.process(state.factory, collector=collector) == (0 if change else 1)
    if change:
        assert state.added == []
    else:
        envelope = state.added[0].envelope
        assert envelope["schema_version"] == service.repo.PIN_POLICY
        assert envelope["target"]["location"] == CONTENT["location"]
        assert envelope["target"]["pin"] == PIN
        assert envelope["target"]["pin_revision"] == 2
        assert state.record.payload == CONTENT


async def test_reserve_waits_for_final_pin(state, monkeypatch):
    enqueue = AsyncMock()
    monkeypatch.setattr(service.repo, "enqueue", enqueue)
    await service.reserve_pin(
        state.db, state.record, SimpleNamespace(payload={"state": "provisional"})
    )
    enqueue.assert_not_awaited()
    await service.reserve_pin(state.db, state.record, SimpleNamespace(payload=PIN))
    assert enqueue.call_args.kwargs["policy"] == service.repo.PIN_POLICY
