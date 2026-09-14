"""Expired facility views must reach recovery without a current GPS fix."""

# ruff: noqa: F811 -- imported pytest fixtures are intentionally shadowed by test parameters.

from uuid import uuid4

import pytest

from daengs_backend.orchestration.facility_presentation import LOCATION_MESSAGE
from daengs_backend.orchestration.semantic import GeminiSemanticRouter
from daengs_backend.orchestration.service import AssistantOrchestrationService
from daengs_backend.routers import assistant
from tests.place.api.test_assistant_conversation import connected, request  # noqa: F401
from tests.place.api.test_conversation import harness, manual_body  # noqa: F401


async def test_expired_facility_without_current_gps_reaches_recovery_signal(connected):
    client, _, _, calls, _, _ = connected
    initial = (await client.post("/app/places/conversation", json=manual_body())).json()
    body = request(initial)
    body["facility"]["session_id"] = str(uuid4())
    # A current device fix is optional when continuing an existing facility view.
    result = (await client.post("/assistant/query", json=body)).json()
    assert any(
        item.get("error", {}).get("kind") == "facility_expired" for item in result["results"]
    ), result
    assert not calls


@pytest.mark.parametrize("selected", [["place"], ["place", "walk"], []])
async def test_expired_view_retains_routing_hint_without_disclosing_ids(
    connected,
    monkeypatch,
    selected,
):
    client, _, _, calls, _, _ = connected
    initial = (await client.post("/app/places/conversation", json=manual_body())).json()
    body = request(initial, "다른 곳 보여줘" if selected else "고마워")
    body["facility"]["session_id"] = str(uuid4())
    body.pop("requested_capability")

    async def select(prompt):
        assert "FACILITY_VIEW:" in prompt
        assert body["facility"]["session_id"] not in prompt
        assert "37.5" not in prompt
        return (
            {"execute": selected, "handoffs": []}
            if selected
            else {"execute": [], "handoffs": [], "social_intent": "thanks"}
        )

    monkeypatch.setattr(
        assistant,
        "build_orchestrator",
        lambda **kw: AssistantOrchestrationService(
            engine=kw.get("engine"), semantic_router=GeminiSemanticRouter(generate=select)
        ),
    )
    result = (await client.post("/assistant/query", json=body)).json()
    if "walk" in selected:
        assert result["status"] == "CLARIFY" and result["results"] == []
        assert result["message"] == result["clarify"]["question"] == LOCATION_MESSAGE
        assert result["clarify"]["missing"] == ["location.lat", "location.lon"]
    elif selected:
        assert result["results"][0]["error"]["kind"] == "facility_expired"
        assert "세션" not in result["message"]
    else:
        assert result["status"] == "ANSWERED" and result["results"] == []
    assert not calls


async def test_first_facility_request_without_location_uses_user_language(connected):
    client, _, _, calls, _, _ = connected
    result = (
        await client.post(
            "/assistant/query",
            json={
                "query": "주변 카페 찾아줘",
                "requested_capability": "place",
                "facility": {"client_request_id": str(uuid4())},
            },
        )
    ).json()
    assert result["status"] == "CLARIFY" and not calls
    assert result["message"] == result["clarify"]["question"] == LOCATION_MESSAGE
