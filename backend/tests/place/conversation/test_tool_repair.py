"""One bounded native tool correction before any facility execution."""

import asyncio
import json

import httpx
import pytest

from daengs_place.place.providers.conversation_gemini import GeminiConversation
from daengs_place.place.providers.gemini import (
    GeminiIntentProposerResponseError,
    GeminiIntentProposerTimeoutError,
)
from tests.place.conversation.test_policy import chat, offer
from tests.place.conversation.test_scope import initial


def response(arguments, *, name="propose_facility_turn", **kwargs):
    return httpx.Response(
        200,
        json={
            "status": "requires_action",
            "steps": [
                {
                    "type": "function_call",
                    "id": "call-1",
                    "name": name,
                    "arguments": arguments,
                    **kwargs,
                }
            ],
        },
    )


def action(**overrides):
    return {"kind": "facility_action", "goal": "show", "changes": {"radius_m": 5000}, **overrides}


@pytest.mark.parametrize(
    "invalid",
    [
        action(changes={"radius_m": -1}),
        action(changes={"kinds": {"operation": "set", "values": ["not_a_kind"]}}),
        action(kind="facility_state"),
        action(**{"private-extra-field": "private-value"}),
        {"goal": "show"},
        None,
    ],
)
async def test_one_native_correction_then_one_search_without_logging_values(invalid, caplog):
    wires = []
    service, searcher, before = await initial()

    def transport(request):
        wires.append(json.loads(request.content))
        assert len(searcher.calls) == 1  # No execution before both proposals are validated.
        return response(invalid if len(wires) == 1 else action(), thought_signature="signature")

    service.planner = GeminiConversation(
        "private-key", "test-model", transport=httpx.MockTransport(transport)
    )
    after = await chat(service, before.state, "private-query")
    assert len(wires) == 2 and len(searcher.calls) == 2
    assert after.state.filters.spatial.radius_m == 5000
    assert after.receipt.execution == "searched"
    history = wires[1]["input"]
    assert json.loads(history[0]["content"][0]["text"])["query"] == "private-query"
    assert history[1]["thought_signature"] == "signature"
    assert history[2]["type"] == "function_result" and history[2]["call_id"] == "call-1"
    assert history[2]["is_error"] is True
    assert json.loads(history[2]["result"][0]["text"])["code"] == "invalid_arguments"
    assert "facility_tool_validation_failed" in caplog.text
    assert not any(
        secret in caplog.text
        for secret in (
            "private-key",
            "private-query",
            "private-value",
            "private-extra-field",
        )
    )


@pytest.mark.parametrize(
    "steps",
    [
        None,
        [],
        [{"type": "text", "text": "untrusted prose"}],
        [
            {"type": "function_call", "name": "other_tool", "arguments": {}},
        ],
    ],
)
async def test_malformed_call_envelope_gets_one_correction_without_exposing_prose(steps):
    wires = []

    def transport(request):
        wires.append(json.loads(request.content))
        return (
            httpx.Response(200, json={"status": "completed", "steps": steps})
            if len(wires) == 1
            else response(action())
        )

    service, searcher, before = await initial(
        GeminiConversation("key", "model", transport=httpx.MockTransport(transport))
    )
    after = await chat(service, before.state, "조금 더 멀리")
    assert len(wires) == 2 and len(searcher.calls) == 2
    assert after.receipt.execution == "searched"
    assert "untrusted prose" not in json.dumps(wires[1], ensure_ascii=False)


async def test_exhausted_pending_repair_preserves_exact_proposal_and_never_searches():
    service, _, searcher, _, pending = await offer()
    calls = []

    def transport(request):
        calls.append(request)
        return response({"decision": "invalid"}, name="classify_pending_decision")

    service.planner = GeminiConversation("key", "model", transport=httpx.MockTransport(transport))
    after = await chat(service, pending.state, "응")
    assert len(calls) == 2 and len(searcher.calls) == 1
    assert after.receipt.code == "invalid_plan"
    assert after.state.pending_proposal.model_dump(
        exclude={"revision"}
    ) == pending.state.pending_proposal.model_dump(exclude={"revision"})
    assert after.state.pending_proposal.revision == after.state.revision
    assert after.state.filters == pending.state.filters


async def test_repaired_pending_decision_executes_only_previously_stored_candidate():
    service, _, searcher, _, pending = await offer()
    calls = []

    def transport(request):
        calls.append(request)
        return response(
            {"decision": "invalid" if len(calls) == 1 else "accept"},
            name="classify_pending_decision",
        )

    service.planner = GeminiConversation("key", "model", transport=httpx.MockTransport(transport))
    after = await chat(service, pending.state, "응")
    assert len(calls) == 2 and len(searcher.calls) == 2
    assert after.state.filters == pending.state.pending_proposal.candidate
    assert after.state.pending_proposal is None


@pytest.mark.parametrize("failure", ["http", "timeout", "cancel"])
async def test_provider_failures_are_not_retried(failure):
    calls = []

    def transport(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("secret-address")
        if failure == "cancel":
            raise asyncio.CancelledError()
        return httpx.Response(503)

    provider = GeminiConversation("key", "model", transport=httpx.MockTransport(transport))
    expected = {
        "http": GeminiIntentProposerResponseError,
        "timeout": GeminiIntentProposerTimeoutError,
        "cancel": asyncio.CancelledError,
    }[failure]
    with pytest.raises(expected):
        await provider._plan({"query": "찾아줘"})
    assert len(calls) == 1


async def test_repair_shares_original_deadline():
    calls = []

    async def transport(request):
        calls.append(request)
        if len(calls) == 1:
            return response(action(changes={"radius_m": 0}))
        await asyncio.sleep(1)
        return response(action())

    provider = GeminiConversation(
        "key", "model", transport=httpx.MockTransport(transport), timeout=0.05
    )
    with pytest.raises(GeminiIntentProposerTimeoutError):
        await provider._plan({"query": "찾아줘"})
    assert len(calls) == 2
