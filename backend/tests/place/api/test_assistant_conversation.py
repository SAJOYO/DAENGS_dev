"""The shared assistant executes the same owner-bound facility session as the map."""

from uuid import UUID, uuid4

import pytest

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.orchestration.adapters import facility as facility_adapter
from daengs_backend.orchestration.semantic import GeminiSemanticRouter
from daengs_backend.orchestration.service import AssistantOrchestrationService
from daengs_backend.routers import assistant
from tests.place.api.test_conversation import harness, manual_body  # noqa: F401

OWNER = UUID("c464e662-5fab-4519-8e37-006436068380")


@pytest.fixture
async def connected(harness, monkeypatch):  # noqa: F811 - imported pytest fixture
    client, store, searcher, calls, plans, app = harness
    from daengs_backend.routers import facility_discovery

    app.include_router(assistant.router)
    app.dependency_overrides[facility_discovery.facility_owner] = lambda: str(OWNER)
    checked = []

    async def active(owner):
        checked.append(owner)

    async def measure(factory, *, run, **kwargs):
        return await run()

    monkeypatch.setattr(facility_adapter, "require_active_facility_owner", active)
    monkeypatch.setattr(assistant.metrics_service, "measured", measure)

    async def bookmarks(owner):
        return []

    service = app.dependency_overrides[assistant.get_facility_conversation_service]()
    monkeypatch.setattr(service, "bookmark_keys", bookmarks)
    client.headers["Authorization"] = "Bearer " + create_access_token(OWNER, SubjectType.APP)
    yield client, store, searcher, calls, plans, checked


def request(previous, query="첫 번째 골라줘"):
    return {
        "query": query,
        "requested_capability": "place",
        "facility": {
            "client_request_id": str(uuid4()),
            "session_id": previous["session_id"],
            "expected_revision": previous["revision"],
            "visible_order": previous["display_order"],
        },
    }


async def test_map_to_assistant_uses_same_session_and_exact_retry_does_not_execute_twice(connected):
    client, _, _, calls, _, checked = connected
    initial = (await client.post("/app/places/conversation", json=manual_body())).json()
    body = request(initial)
    response = await client.post("/assistant/query", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "ANSWERED", result
    assert checked == [OWNER]
    data = result["results"][0]["data"]
    assert set(data) == {"contract_version", "answer", "facility"}
    assert data["facility"]["session_id"] == initial["session_id"]
    recovered = await client.post(
        "/app/places/conversation/recover",
        json={k: v for k, v in data["facility"].items() if k != "revision"},
    )
    assert recovered.status_code == 200
    saved = recovered.json()
    assert saved["revision"] == initial["revision"] + 1
    assert saved["selected"] == initial["display_order"][0]
    assert saved["answer"]["text"] == result["message"]
    again = (await client.post("/assistant/query", json=body)).json()
    assert again["results"][0]["data"] == data
    assert len(calls) == 1


async def test_first_assistant_search_bootstraps_once_and_can_be_recovered(connected):
    client, _, _, calls, _, _ = connected
    body = {
        "query": "하나 골라줘",
        "requested_capability": "place",
        "location": {"lat": 37.5, "lon": 127.0},
        "facility": {"client_request_id": str(uuid4())},
    }
    first = (await client.post("/assistant/query", json=body)).json()
    assert first["status"] == "ANSWERED", first
    assert first["results"][0]["data"]["facility"]["revision"] == 2
    retry = (await client.post("/assistant/query", json=body)).json()
    assert retry["results"][0]["data"] == first["results"][0]["data"]
    assert len(calls) == 1
    conflicting = (
        await client.post("/assistant/query", json={**body, "query": "다른 곳 보여줘"})
    ).json()
    assert conflicting["results"][0]["error"]["kind"] == "facility_conflict"
    assert len(calls) == 1


async def test_stale_revision_and_missing_session_never_reach_facility_model(connected):
    client, _, _, calls, _, _ = connected
    initial = (await client.post("/app/places/conversation", json=manual_body())).json()
    body = request(initial)
    body["facility"]["expected_revision"] += 1
    failed = (await client.post("/assistant/query", json=body)).json()
    assert failed["results"][0]["error"]["kind"] == "facility_conflict"
    body = request(initial)
    body["facility"]["session_id"] = str(uuid4())
    body["location"] = {"lat": 37.5, "lon": 127.0}
    failed = (await client.post("/assistant/query", json=body)).json()
    assert failed["results"][0]["error"]["kind"] == "facility_expired"
    assert calls == []


async def test_another_member_cannot_read_or_change_the_map_session(connected):
    client, _, _, calls, _, checked = connected
    initial = (await client.post("/app/places/conversation", json=manual_body())).json()
    other = uuid4()
    client.headers["Authorization"] = "Bearer " + create_access_token(other, SubjectType.APP)
    body = request(initial)
    body["location"] = {"lat": 37.5, "lon": 127.0}
    result = (await client.post("/assistant/query", json=body)).json()
    assert result["results"][0]["error"]["kind"] == "facility_expired"
    assert initial["session_id"] not in str(result)
    assert checked == [other]
    assert not calls


async def test_admin_cannot_submit_a_member_facility_context(connected):
    client, _, _, calls, _, checked = connected
    client.headers["Authorization"] = "Bearer " + create_access_token(
        OWNER, SubjectType.ADMIN, role="VIEWER"
    )
    response = await client.post(
        "/assistant/query",
        json={
            "query": "카페 찾아줘",
            "facility": {"client_request_id": str(uuid4())},
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FACILITY_APP_USER_ONLY"
    assert not checked and not calls


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_revision": 0},
        {"owner": str(OWNER)},
        {"initial_search": {"lat": 0}},
    ],
)
async def test_facility_context_rejects_incoherent_or_client_owned_state(connected, changes):
    client, _, _, calls, _, checked = connected
    initial = (await client.post("/app/places/conversation", json=manual_body())).json()
    body = request(initial)
    body["facility"].update(changes)
    response = await client.post("/assistant/query", json=body)
    assert response.status_code == 422
    assert not checked and not calls


async def test_facility_hint_routes_followup_without_leaking_view_and_new_social_turn_does_not_execute(
    connected, monkeypatch
):
    client, _, _, calls, _, _ = connected
    initial = (await client.post("/app/places/conversation", json=manual_body())).json()
    prompts = []

    async def select(prompt):
        prompts.append(prompt)
        return {"execute": [], "handoffs": [], "social_intent": "thanks"}

    monkeypatch.setattr(
        assistant,
        "build_orchestrator",
        lambda **kw: AssistantOrchestrationService(
            engine=kw.get("engine"), semantic_router=GeminiSemanticRouter(generate=select)
        ),
    )
    body = request(initial, "고마워")
    body.pop("requested_capability")
    result = (await client.post("/assistant/query", json=body)).json()
    assert result["status"] == "ANSWERED", result
    assert result["results"] == []
    assert "FACILITY_VIEW:" in prompts[0]
    assert initial["session_id"] not in prompts[0]
    assert str(OWNER) not in prompts[0]
    assert prompts[0].endswith("USER_QUERY: 고마워\n")
    assert not calls


@pytest.mark.parametrize("capabilities", [["place"], ["place", "walk"]])
async def test_implicit_facility_followup_uses_saved_origin_but_walk_still_needs_device_location(
    connected, monkeypatch, capabilities
):
    client, _, _, calls, _, _ = connected
    initial = (await client.post("/app/places/conversation", json=manual_body())).json()

    async def select(prompt):
        assert "FACILITY_VIEW:" in prompt
        return {"execute": capabilities, "handoffs": []}

    monkeypatch.setattr(
        assistant,
        "build_orchestrator",
        lambda **kw: AssistantOrchestrationService(
            engine=kw.get("engine"), semantic_router=GeminiSemanticRouter(generate=select)
        ),
    )
    body = request(initial, "하나 골라줘")
    body.pop("requested_capability")
    result = (await client.post("/assistant/query", json=body)).json()
    if "walk" in capabilities:
        assert result["status"] == "CLARIFY", result
        assert result["results"] == [] and not calls
    else:
        assert result["status"] == "ANSWERED", result
        assert result["results"][0]["data"]["facility"]["session_id"] == initial["session_id"]
        assert len(calls) == 1
