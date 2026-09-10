"""Confirmation transactions cross the real gateway/place HTTP and CAS boundary."""

import asyncio
import json

from daengs_place.api import conversation_internal
from daengs_place.place.conversation.intent import PendingDecision
from tests.place.api import test_conversation as support

harness = support.harness
URL = "/app/places/conversation"


async def offered(harness):
    client, _, _, _, plans, _ = harness
    first = (await client.post(URL, json=support.manual_body())).json()
    plans.append(
        {
            "goal": "show",
            "unsupported": ["quiet"],
            "changes": {
                "kinds": {"operation": "set", "values": ["cafe"]},
                "parking": "required_true",
            },
        }
    )
    pending = (
        await client.post(URL, json=support.chat_body(first, "조용하고 주차되는 카페"))
    ).json()
    assert pending["receipt"]["action"] == "await_confirmation"
    assert pending["filters"] == first["filters"]
    return pending


async def test_offer_and_accept_are_recoverable_and_retry_does_not_replan(harness):
    client, store, searcher, calls, plans, _ = harness
    pending = await offered(harness)
    saved = json.loads(store.items[pending["session_id"]])
    proposal = saved["state"]["pending_proposal"]
    assert proposal["revision"] == pending["revision"] == 2
    assert saved["pending"] is None  # Inflight lease is unrelated to user confirmation.
    answer = (await client.post(URL + "/answer", json=support.answer_body(pending))).json()
    assert answer["answer"]["text"] == proposal["question"]
    assert len(calls) == 1
    plans.append({"decision": "accept"})
    request = support.chat_body(pending, "응")
    accepted = (await client.post(URL, json=request)).json()
    assert accepted["filters"] == proposal["candidate"]
    assert len(calls) == 2 and len(searcher.calls) == 2
    assert calls[-1]["tools"][0]["name"] == "classify_pending_decision"
    assert (await client.post(URL, json=request)).json() == accepted
    assert len(calls) == 2 and len(searcher.calls) == 2
    assert (
        await client.post(URL + "/recover", json=support.recovery_body(accepted))
    ).json() == accepted


async def test_foreign_owner_and_stale_revision_cannot_accept_proposal(harness):
    client, _, _, calls, _, app = harness
    pending = await offered(harness)
    app.dependency_overrides[support.gateway.facility_owner] = lambda: "other-owner"
    assert (await client.post(URL, json=support.chat_body(pending, "응"))).status_code == 410
    app.dependency_overrides[support.gateway.facility_owner] = lambda: "owner-a"
    manual = support.manual_body(pending)
    manual["manual"]["radius_m"] = 1000
    latest = (await client.post(URL, json=manual)).json()
    assert (await client.post(URL, json=support.chat_body(pending, "응"))).status_code == 409
    assert len(calls) == 1
    assert (
        await client.post(URL + "/recover", json=support.recovery_body(pending))
    ).json() == latest


async def test_manual_edit_wins_over_inflight_acceptance(harness, monkeypatch):
    client, store, _, _, _, _ = harness
    pending = await offered(harness)
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowConsent:
        async def decide_pending(self, request):
            entered.set()
            await release.wait()
            return PendingDecision(decision="accept")

    monkeypatch.setattr(conversation_internal, "provider", lambda: SlowConsent())
    accepting = asyncio.create_task(client.post(URL, json=support.chat_body(pending, "응")))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        manual = support.manual_body(pending)
        manual["manual"]["radius_m"] = 1000
        latest = (await client.post(URL, json=manual)).json()
    finally:
        release.set()
    assert (await accepting).status_code == 409
    saved = json.loads(store.items[pending["session_id"]])
    assert saved["state"]["pending_proposal"] is None
    assert saved["state"]["filters"] == latest["filters"]
    assert saved["revision"] == latest["revision"]
