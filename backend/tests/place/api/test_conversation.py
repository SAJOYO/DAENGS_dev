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
