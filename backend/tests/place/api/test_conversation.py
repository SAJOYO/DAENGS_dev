import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from daengs_backend.routers import facility_discovery as gateway
from daengs_backend.services.facility_conversation import (
    FacilityConversationService,
    get_facility_conversation_service,
)
from daengs_place.api import conversation_internal
from daengs_place.core.db import get_session
from daengs_place.main import app as place_app
from daengs_place.place.conversation.contract import TurnPlan
from daengs_place.place.conversation.service import ConversationService
from daengs_place.place.providers.conversation_gemini import GeminiConversation
from tests.place.support.conversation import Searcher
from tests.place.support.session_store import MemorySessions


def manual_body(previous=None):
    body = {
        "client_request_id": str(uuid4()),
        "mode": "manual",
        "manual": {"lat": 37.5, "lng": 127.0, "radius_m": 3000, "kinds": ["shopping", "pet_shop"]},
    }
    if previous:
        body.update(session_id=previous["session_id"], expected_revision=previous["revision"])
    return body


def chat_body(previous, query="아무 데나 하나 골라줘"):
    return {
        "client_request_id": str(uuid4()),
        "mode": "chat",
        "query": query,
        "session_id": previous["session_id"],
        "expected_revision": previous["revision"],
        "visible_order": previous["display_order"],
    }


def answer_body(previous):
    return {key: previous[key] for key in ("session_id", "revision", "client_request_id")}


def recovery_body(previous):
    return {key: previous[key] for key in ("session_id", "client_request_id")}


@pytest.fixture
async def harness(monkeypatch):
    store, searcher, model_calls = MemorySessions(), Searcher(), []
    plans = []

    async def gemini(request):
        payload = json.loads(request.content)
        model_calls.append(payload)
        if "tools" in payload:
            assert [tool["name"] for tool in payload["tools"]] == ["propose_facility_turn"]
            plan = plans.pop(0) if plans else {"goal": "pick_one"}
            steps = [{"type": "function_call", "name": "propose_facility_turn", "arguments": plan}]
        else:
            assert "tools" not in payload
            receipt = json.loads(payload["input"])["receipt"]
            evidence = receipt["evidence"]
            text = evidence.get("place", "") + "을 살펴보세요. " + evidence.get("distance", "")
            steps = [
                {
                    "type": "model_output",
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"text": text, "evidence_ids": list(evidence)}, ensure_ascii=False
                            ),
                        }
                    ],
                }
            ]
        return httpx.Response(200, json={"status": "completed", "steps": steps})

    model = GeminiConversation("test-key", "test-model", transport=httpx.MockTransport(gemini))
    monkeypatch.setattr(conversation_internal, "provider", lambda: model)
    monkeypatch.setattr(
        conversation_internal,
        "ConversationService",
        lambda planner: ConversationService(planner, searcher=searcher),
    )

    async def no_db():
        yield None

    place_app.dependency_overrides[get_session] = no_db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=place_app), base_url="http://place"
    ) as place_client:
        service = FacilityConversationService(
            client=place_client, store=store, base_url="http://place", timeout=5
        )
        app = FastAPI()
        app.include_router(gateway.router)
        app.dependency_overrides[gateway.facility_owner] = lambda: "owner-a"
        app.dependency_overrides[get_facility_conversation_service] = lambda: service
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ) as client:
            yield client, store, searcher, model_calls, plans, app
    place_app.dependency_overrides.pop(get_session, None)


async def test_manual_to_gemini_plan_to_cached_pick_to_answer_through_both_http_services(harness):
    client, store, searcher, calls, plans, _ = harness
    initial = (await client.post("/app/places/conversation", json=manual_body())).json()
    assert len(searcher.calls) == 1 and calls == []
    request = chat_body(initial)
    response = await client.post("/app/places/conversation", json=request)
    assert response.status_code == 200, response.text
    picked = response.json()
    assert picked["selected"]["ref"] == "first"
    assert picked["receipt"]["execution"] == "reused"
    assert picked["answer"] is None and picked["answer_status"] == "pending"
    assert len(calls) == 1
    picked = (await client.post("/app/places/conversation/answer", json=answer_body(picked))).json()
    assert picked["answer"]["source"] == "llm"
    assert picked["filters"]["candidate_kinds"] == ["shopping", "pet_shop"]
    assert len(searcher.calls) == 1 and len(calls) == 2
    assert json.loads(calls[0]["input"])["current_state"]["candidate_kinds"] == [
        "shopping",
        "pet_shop",
    ]
    assert json.loads(calls[1]["input"])["committed_revision"] == picked["revision"]
    again = await client.post("/app/places/conversation", json=request)
    assert again.json() == picked
    assert len(calls) == 2
    plans.append({"goal": "explain", "reference_index": 2})
    explanation = (
        await client.post("/app/places/conversation", json=chat_body(picked, "두 번째는 왜?"))
    ).json()
    assert explanation["selected"]["ref"] == "second"
    assert len(searcher.calls) == 1
    saved = json.loads(store.items[picked["session_id"]])
    assert saved["revision"] == explanation["revision"] == 3


async def test_owner_and_revision_guard_run_before_model(harness):
    client, _, _, calls, _, app = harness
    initial = (await client.post("/app/places/conversation", json=manual_body())).json()
    stale = chat_body(initial)
    stale["expected_revision"] = 0
    assert (await client.post("/app/places/conversation", json=stale)).status_code == 409
    app.dependency_overrides[gateway.facility_owner] = lambda: "other-owner"
    assert (
        await client.post("/app/places/conversation", json=chat_body(initial))
    ).status_code == 410
    assert calls == []


async def test_newer_manual_request_prevents_old_ai_from_committing_server_state(
    harness, monkeypatch
):
    client, store, _, calls, _, _ = harness
    initial = (await client.post("/app/places/conversation", json=manual_body())).json()
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowPlanner:
        async def plan(self, request):
            entered.set()
            await release.wait()
            return TurnPlan(goal="pick_one")

    monkeypatch.setattr(conversation_internal, "provider", lambda: SlowPlanner())
    pending = asyncio.create_task(client.post("/app/places/conversation", json=chat_body(initial)))
    await entered.wait()
    update = manual_body(initial)
    update["manual"]["kinds"] = ["cafe"]
    latest = await client.post("/app/places/conversation", json=update)
    assert latest.status_code == 200, latest.text
    release.set()
    old = await pending
    assert old.status_code == 409
    saved = json.loads(store.items[initial["session_id"]])
    assert saved["state"]["filters"]["candidate_kinds"] == ["cafe"]
    assert saved["revision"] == 2
    assert calls == []


async def test_committed_state_is_visible_while_answer_is_blocked_and_new_manual_wins(
    harness, monkeypatch
):
    client, _, _, _, _, app = harness
    initial = (await client.post("/app/places/conversation", json=manual_body())).json()
    committed = (await client.post("/app/places/conversation", json=chat_body(initial))).json()
    service = app.dependency_overrides[get_facility_conversation_service]()
    exchange = service.exchange
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_answer(step, payload):
        if step == "answer":
            entered.set()
            await release.wait()
        return await exchange(step, payload)

    monkeypatch.setattr(service, "exchange", slow_answer)
    answering = asyncio.create_task(
        client.post("/app/places/conversation/answer", json=answer_body(committed))
    )
    await entered.wait()
    recovered = await client.post("/app/places/conversation/recover", json=recovery_body(committed))
    assert recovered.json() == committed
    update = manual_body(committed)
    update["manual"]["radius_m"] = 1000
    latest = (await client.post("/app/places/conversation", json=update)).json()
    assert latest["revision"] == 3 and latest["filters"]["spatial"]["radius_m"] == 1000
    release.set()
    assert (await answering).status_code == 409
    assert (
        await client.post("/app/places/conversation/recover", json=recovery_body(latest))
    ).json() == latest


async def test_lost_initial_and_later_reply_retry_same_identity_without_reexecution(harness):
    client, _, searcher, calls, _, _ = harness
    first_request = manual_body()
    first = (await client.post("/app/places/conversation", json=first_request)).json()
    assert (await client.post("/app/places/conversation", json=first_request)).json() == first
    assert (
        await client.post(
            "/app/places/conversation/recover",
            json={"client_request_id": first_request["client_request_id"]},
        )
    ).json() == first
    request = chat_body(first, "두 번째 골라줘")
    committed = (await client.post("/app/places/conversation", json=request)).json()
    assert (await client.post("/app/places/conversation", json=request)).json() == committed
    assert len(searcher.calls) == 1 and len(calls) == 1
    latest = (await client.post("/app/places/conversation", json=manual_body(committed))).json()
    assert (await client.post("/app/places/conversation", json=request)).status_code == 409
    assert (
        await client.post("/app/places/conversation/recover", json=recovery_body(committed))
    ).json() == latest
    assert len(calls) == 1


async def test_manual_failure_returns_old_conditions_together_with_old_results(harness):
    client, _, searcher, _, _, _ = harness
    first = (await client.post("/app/places/conversation", json=manual_body())).json()
    searcher.error = True
    update = manual_body(first)
    update["manual"].update(radius_m=1000, name_query="새 이름", kinds=["cafe"])
    failed = (await client.post("/app/places/conversation", json=update)).json()
    assert failed["revision"] == 2
    assert failed["receipt"]["execution"] == "failed"
    assert failed["filters"] == first["filters"] and failed["search"] == first["search"]


async def test_expired_session_restores_full_filters_in_new_session_without_old_history(harness):
    client, store, searcher, calls, plans, _ = harness
    first = (await client.post("/app/places/conversation", json=manual_body())).json()
    plans.append(
        {
            "goal": "show",
            "changes": {
                "upsert_all": [
                    {"id": "parking", "capability": "operations.parking", "op": "eq", "value": True}
                ],
                "upsert_any": [
                    {
                        "id": "shopping",
                        "all": [
                            {
                                "id": "kind",
                                "capability": "purpose.kind",
                                "op": "in",
                                "value": ["shopping"],
                            }
                        ],
                    }
                ],
            },
        }
    )
    filtered = (
        await client.post("/app/places/conversation", json=chat_body(first, "주차되는 쇼핑시설"))
    ).json()
    assert filtered["filters"]["hard"]["all"] and filtered["filters"]["hard"]["any"]
    store.items.pop(first["session_id"])
    assert (
        await client.post("/app/places/conversation", json=chat_body(filtered))
    ).status_code == 410
    restored = await client.post(
        "/app/places/conversation",
        json={
            "client_request_id": str(uuid4()),
            "mode": "restore",
            "restore_filters": filtered["filters"],
        },
    )
    assert restored.status_code == 200, restored.text
    restored = restored.json()
    assert restored["filters"] == filtered["filters"]
    assert restored["session_id"] != filtered["session_id"] and restored["revision"] == 1
    assert restored["receipt"]["snapshot_id"] != filtered["receipt"]["snapshot_id"]
    assert restored["answer_status"] == "none" and len(calls) == 1
    assert len(searcher.calls) == 3
    assert json.loads(store.items[restored["session_id"]])["state"]["history"] == []
    continued = await client.post("/app/places/conversation", json=chat_body(restored))
    assert continued.status_code == 200


async def test_recovery_and_answer_are_owner_bound_and_restore_rejects_invalid_filters(harness):
    client, _, _, _, _, app = harness
    first = (await client.post("/app/places/conversation", json=manual_body())).json()
    app.dependency_overrides[gateway.facility_owner] = lambda: "other-owner"
    assert (
        await client.post("/app/places/conversation/recover", json=recovery_body(first))
    ).status_code == 410
    assert (
        await client.post("/app/places/conversation/answer", json=answer_body(first))
    ).status_code == 410
    bad = {**first["filters"], "spatial": {"lat": 37.5, "lng": 127, "radius_m": -1}}
    assert (
        await client.post(
            "/app/places/conversation",
            json={"client_request_id": str(uuid4()), "mode": "restore", "restore_filters": bad},
        )
    ).status_code == 422
