"""Provider outcomes and transaction/lease boundaries; SQL is tested separately."""

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from daengs_backend.config import settings
from daengs_backend.services import walk_entry_context as service
from daengs_backend.services import walk_entry_context_source as source
from tests.walk.support.entry_context import CONTENT, NOW, body


@pytest.mark.parametrize(
    "tag,content,reason",
    [
        ("space.facility", {**CONTENT, "location": None}, "no_location"),
        ("space.park", CONTENT, "provider_not_connected"),
        ("space.river", CONTENT, "provider_not_connected"),
        ("environment.weather", CONTENT, "provider_not_connected"),
    ],
)
async def test_missing_context_does_not_make_http_calls(tag, content, reason):
    client = SimpleNamespace(post=AsyncMock())
    result = await source.collect(tag, content, client=client)
    assert result.status == "not_requested" and result.reason == reason
    assert result.retrieved_at is None
    client.post.assert_not_awaited()


@pytest.mark.parametrize(
    "options,status", [({}, "known"), ({"empty": True}, "empty"), ({"truncated": True}, "partial")]
)
async def test_structured_lookup_preserves_evidence_not_inferred_visit(options, status):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=body(**options))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await source.collect("space.facility", CONTENT, client=client)
    assert result.status == status
    assert not result.payload["visit_confirmed"]
    assert requests == [
        {
            "lat": 37.5,
            "lng": 127,
            "radius_m": 250,
            "kinds": list(source.KINDS),
            "limit_per_kind": 10,
        }
    ]
    assert "discard" not in json.dumps(result.payload)
    assert "사용자가 남긴 원문" not in json.dumps(requests)


@pytest.mark.parametrize("code,retryable", [(403, False), (429, True), (503, True)])
async def test_http_failures_are_bounded_and_do_not_copy_error_bodies(code, retryable):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(code, text="sensitive provider error")
        )
    ) as client:
        result = await source.collect("space.facility", CONTENT, client=client)
    assert result.reason == f"http_{code}" and result.retryable == retryable
    assert result.payload is None


async def test_bad_response_is_not_a_successful_empty_result():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"groups": []}))
    ) as client:
        result = await source.collect("space.facility", CONTENT, client=client)
    assert result.status == "unavailable" and result.reason == "invalid_response"


async def test_provider_runs_with_no_session_and_result_keeps_original_anchor(state):
    async def collector(tag, content):
        assert not state.active[0]
        assert content == CONTENT
        return source.Collected("known", payload={"items": []}, retrieved_at=NOW.isoformat())

    assert await service.process(state.factory, collector=collector) == 1
    assert state.db.commit.await_count == 2
    assert state.job.state == "completed"
    envelope = state.added[0].envelope
    assert envelope["target"]["event_at"] == CONTENT["recorded_at"]
    assert envelope["target"]["location"] == CONTENT["location"]
    assert envelope["target"]["revision"] == 1
    assert envelope["payload_sha256"] == source.digest({"items": []})
    assert state.record.payload == CONTENT


@pytest.mark.parametrize("change", ["revision", "delete", "token", "expired", "cancelled"])
async def test_late_reply_cannot_attach_to_changed_or_deleted_record(state, change):
    if change == "revision":
        state.record.revision = 2
    elif change == "delete":
        state.record.payload = None
    elif change == "token":
        state.job.lease_token = uuid.uuid4()
    elif change == "expired":
        state.job.lease_until = NOW - timedelta(seconds=1)
    else:
        state.job.state = "cancelled"
    assert not await service.finish(
        state.factory, state.ticket, source.Collected("empty", payload={})
    )
    assert not state.added


@pytest.mark.parametrize("attempt,expected", [(1, "pending"), (2, "pending"), (3, "failed")])
async def test_transient_failure_retries_only_up_to_three_attempts(state, attempt, expected):
    state.job.attempts = attempt
    assert await service.finish(
        state.factory,
        state.ticket,
        source.Collected("unavailable", "transport_error", retryable=True),
    )
    assert state.job.state == expected
    assert state.job.lease_token is None and state.job.lease_until is None
    assert state.added[0].attempt == attempt


async def test_disabled_worker_does_not_open_database(state, monkeypatch):
    monkeypatch.setattr(settings, "walk_entry_context_enabled", False)
    assert await service.process(state.factory) == 0
    state.db.scalar.assert_not_awaited()
