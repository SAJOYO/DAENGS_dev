"""Pure social intent (greeting / thanks / goodbye) on the semantic path.

Every test uses a fake Gemini transport — no provider spend. The router only
classifies `social_intent`; the reply is a fixed template from social.py, built
before any RoutePlan exists. Capability intent always takes precedence, so any
mixed utterance keeps its existing Training / Walk / Gait routing, and the
schema rejects a decision that carries both a social intent and destinations.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.orchestration.contracts import (
    AssistantStatus,
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    PrincipalContext,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.semantic import (
    PROMPT_VERSION,
    GeminiSemanticRouter,
    SemanticRoutingDecision,
    SemanticRoutingError,
    build_semantic_router_prompt,
    validate_semantic_decision,
)
from daengs_backend.orchestration.service import AssistantOrchestrationService
from daengs_backend.orchestration.social import build_social_response, social_message
from daengs_backend.routers import assistant as assistant_router

PRINCIPAL = PrincipalContext(subject="test-user", kind="APP_USER")
UNSUPPORTED_MESSAGE = "실행하거나 안내할 수 있는 기능이 없습니다."


class ScriptedTransport:
    def __init__(self, *outputs: object) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> object:
        self.prompts.append(prompt)
        result = self.outputs.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeAdapter:
    def __init__(self, capability: CapabilityName) -> None:
        self.capability = capability
        self.calls: list[CapabilityRequest] = []

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        self.calls.append(request)
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={"answer": f"{self.capability.value} answer"},
            elapsed_ms=1,
        )


class RecordingEngine(OrchestrationEngine):
    """The real Card 1 engine, plus a count of how often the planning layer entered it."""

    def __init__(self, adapters: dict[CapabilityName, FakeAdapter]) -> None:
        super().__init__(adapters)
        self.runs = 0

    async def run(self, **kwargs: Any) -> Any:
        self.runs += 1
        return await super().run(**kwargs)


def decision(
    execute: list[str] = (), handoffs: list[str] = (), social_intent: str | None = None
) -> str:
    return json.dumps(
        {"execute": list(execute), "handoffs": list(handoffs), "social_intent": social_intent}
    )


def build_service(
    *outputs: object,
) -> tuple[AssistantOrchestrationService, ScriptedTransport, RecordingEngine, dict]:
    transport = ScriptedTransport(*outputs)
    adapters = {name: FakeAdapter(name) for name in CapabilityName}
    engine = RecordingEngine(adapters)
    service = AssistantOrchestrationService(
        engine=engine, semantic_router=GeminiSemanticRouter(generate=transport)
    )
    return service, transport, engine, adapters


# --------------------------------------------------------------- A/B/C pure social


@pytest.mark.parametrize(
    ("query", "intent"),
    [
        ("고마워", "thanks"),
        ("고마워요", "thanks"),
        ("도움됐어, 고마워", "thanks"),
        ("안녕", "greeting"),
        ("안녕하세요", "greeting"),
        ("잘가", "goodbye"),
        ("다음에 또 물어볼게", "goodbye"),
    ],
)
async def test_pure_social_intent_is_answered_by_a_template_without_execution(
    query: str, intent: str
) -> None:
    service, transport, engine, adapters = build_service(decision(social_intent=intent))
    response = await service.run(query=query, principal=PRINCIPAL, request_id="rid-social")
    assert response.status == AssistantStatus.ANSWERED
    assert response.message == social_message(intent)
    assert response.request_id == "rid-social"
    assert response.results == [] and response.handoffs == [] and response.clarify is None
    assert len(transport.prompts) == 1  # classified once, never retried
    assert engine.runs == 0  # never entered LangGraph
    assert all(not adapter.calls for adapter in adapters.values())


def test_templates_are_polite_korean_with_at_most_one_daeng() -> None:
    for intent in ("greeting", "thanks", "goodbye"):
        message = social_message(intent)
        assert message.count("댕") == 1
        assert message.endswith(("요, 댕.", "세요, 댕."))
        for internal in ("training", "life", "walk", "skin", "gait", "capability", "RoutePlan"):
            assert internal not in message


def test_thanks_template_matches_the_accepted_tone() -> None:
    assert (
        social_message("thanks")
        == "저야말로 고마워요! 또 궁금한 게 있으면 편하게 물어봐 주세요, 댕."
    )


# ------------------------------------------------------- D/E/F capability precedence


async def test_social_plus_training_routes_to_training_not_a_social_reply() -> None:
    service, _, engine, adapters = build_service(decision(["training"]))
    response = await service.run(
        query="고마워. 그런데 강아지가 자꾸 손을 물어요", principal=PRINCIPAL
    )
    assert response.status == AssistantStatus.ANSWERED
    assert [r.capability for r in response.results] == [CapabilityName.TRAINING]
    assert response.message == "training answer"
    assert response.message != social_message("thanks")
    assert engine.runs == 1
    [request] = adapters[CapabilityName.TRAINING].calls
    assert request.payload.question == "고마워. 그런데 강아지가 자꾸 손을 물어요"


async def test_social_plus_walk_keeps_existing_coordinate_clarify_behavior() -> None:
    service, _, engine, adapters = build_service(decision(["walk"]))
    response = await service.run(query="안녕, 오늘 산책 나가도 괜찮아?", principal=PRINCIPAL)
    assert response.status == AssistantStatus.CLARIFY
    assert response.clarify is not None
    assert response.clarify.missing == ["location.lat", "location.lon"]
    assert response.message != social_message("greeting")
    assert engine.runs == 1
    assert all(not adapter.calls for adapter in adapters.values())


async def test_social_plus_walk_with_coordinates_executes_walk() -> None:
    service, _, _, adapters = build_service(decision(["walk"]))
    response = await service.run(
        query="안녕, 오늘 산책 나가도 괜찮아?",
        principal=PRINCIPAL,
        context={"location": {"lat": 37.5665, "lon": 126.978}},
    )
    assert response.status == AssistantStatus.ANSWERED
    assert len(adapters[CapabilityName.WALK].calls) == 1


async def test_social_plus_gait_is_a_gait_handoff_not_a_thanks_reply() -> None:
    service, _, engine, adapters = build_service(decision(handoffs=["gait"]))
    response = await service.run(query="고마워, 강아지 걷는 영상도 봐줘", principal=PRINCIPAL)
    assert response.status == AssistantStatus.HANDOFF
    assert [(h.target, h.reason) for h in response.handoffs] == [("gait", "video_upload_required")]
    assert response.message != social_message("thanks")
    assert engine.runs == 1
    assert all(not adapter.calls for adapter in adapters.values())


# ---------------------------------------------------------- G unsupported non-social


async def test_unsupported_non_social_request_keeps_existing_empty_behavior() -> None:
    service, _, engine, adapters = build_service(decision())
    response = await service.run(query="비트코인 시세가 어떻게 돼?", principal=PRINCIPAL)
    assert response.status == AssistantStatus.FAILED
    assert response.message == UNSUPPORTED_MESSAGE
    assert response.clarify is None
    assert engine.runs == 1  # the empty RoutePlan still takes the existing path
    assert all(not adapter.calls for adapter in adapters.values())


async def test_explicit_null_social_intent_is_the_same_as_absent() -> None:
    service, _, _, _ = build_service(json.dumps({"execute": [], "handoffs": []}))
    response = await service.run(query="오늘 날씨가 좋네요", principal=PRINCIPAL)
    assert response.status == AssistantStatus.FAILED
    assert response.message == UNSUPPORTED_MESSAGE


# ------------------------------------------------------ H invalid mixed router output


def test_schema_rejects_social_intent_together_with_execute_or_handoffs() -> None:
    assert validate_semantic_decision(decision(["training"], social_intent="thanks")) is None
    assert validate_semantic_decision(decision(handoffs=["gait"], social_intent="thanks")) is None
    assert validate_semantic_decision(decision(social_intent="joke")) is None
    with pytest.raises(ValueError, match="exclusive"):
        SemanticRoutingDecision(execute=["training"], handoffs=[], social_intent="thanks")


def test_social_intent_defaults_to_null_and_is_in_the_provider_schema() -> None:
    assert SemanticRoutingDecision(execute=["training"]).social_intent is None
    assert SemanticRoutingDecision().social_intent is None
    schema = SemanticRoutingDecision.model_json_schema()
    assert "social_intent" in schema["properties"]
    assert set(schema.get("required", [])) == set()
    enum_values = {
        value
        for option in schema["properties"]["social_intent"]["anyOf"]
        for value in option.get("enum", [])
    }
    assert enum_values == {"greeting", "thanks", "goodbye"}


async def test_mixed_invalid_output_uses_the_existing_single_retry() -> None:
    service, transport, engine, adapters = build_service(
        decision(["training"], social_intent="thanks"), decision(social_intent="thanks")
    )
    response = await service.run(query="고마워", principal=PRINCIPAL)
    assert response.status == AssistantStatus.ANSWERED
    assert response.message == social_message("thanks")
    assert len(transport.prompts) == 2
    assert engine.runs == 0
    assert all(not adapter.calls for adapter in adapters.values())


async def test_mixed_invalid_output_twice_is_a_router_failure() -> None:
    service, transport, engine, adapters = build_service(
        decision(["training"], social_intent="thanks"),
        decision(handoffs=["gait"], social_intent="greeting"),
    )
    response = await service.run(query="고마워", principal=PRINCIPAL)
    assert response.status == AssistantStatus.FAILED
    assert response.clarify is None and response.results == [] and response.handoffs == []
    assert "thanks" not in response.message and "training" not in response.message
    assert len(transport.prompts) == 2
    assert engine.runs == 0
    assert all(not adapter.calls for adapter in adapters.values())


async def test_router_raises_after_two_mixed_outputs() -> None:
    router = GeminiSemanticRouter(
        generate=ScriptedTransport(
            decision(["life"], social_intent="goodbye"), decision(["life"], social_intent="goodbye")
        )
    )
    with pytest.raises(SemanticRoutingError):
        await router.select(query="잘가", context={})


# ------------------------------------------------------- I deterministic path unchanged


async def test_requested_capability_still_bypasses_gemini_even_for_social_text() -> None:
    service, transport, engine, adapters = build_service()  # any provider call would IndexError
    response = await service.run(
        query="고마워", principal=PRINCIPAL, requested_capability="training"
    )
    assert response.status == AssistantStatus.ANSWERED
    assert response.message == "training answer"
    assert transport.prompts == []
    assert engine.runs == 1
    assert adapters[CapabilityName.TRAINING].calls[0].payload.question == "고마워"


# ----------------------------------------------------------------- prompt v4 policy


def test_prompt_is_v4_and_states_the_social_rules_without_keyword_lists() -> None:
    assert PROMPT_VERSION == "semantic-router-ko-v4"
    prompt = build_semantic_router_prompt(query="고마워", context={})
    assert "PROMPT_VERSION: semantic-router-ko-v4" in prompt
    assert "social_intent" in prompt
    assert "purely social" in prompt
    assert "leave social_intent null" in prompt
    assert "do not generate conversational prose" in prompt
    # Semantic classification only: no Korean keyword list is handed to the model.
    for keyword in ("고마워", "안녕", "잘가"):
        assert keyword not in prompt.split("USER_QUERY:")[0]


# --------------------------------------------------------- J auth / context boundary


def test_social_response_carries_no_principal_or_context() -> None:
    response = build_social_response(request_id="rid", intent="greeting")
    assert response.model_dump() == {
        "request_id": "rid",
        "status": "ANSWERED",
        "message": social_message("greeting"),
        "results": [],
        "handoffs": [],
        "clarify": None,
    }


async def test_social_reply_never_echoes_principal_or_routing_metadata() -> None:
    service, transport, _, _ = build_service(decision(social_intent="greeting"))
    principal = PrincipalContext(subject="subject-secret-7", kind="APP_USER")
    response = await service.run(
        query="안녕하세요",
        principal=principal,
        context={"source": "assistant", "action": "chat_send", "active_dog_id": "dog-9"},
    )
    assert response.message == social_message("greeting")
    assert "subject-secret-7" not in response.message and "dog-9" not in response.message
    assert "subject-secret-7" not in transport.prompts[0]  # principal never reaches the prompt


def _post(service: AssistantOrchestrationService, body: dict[str, Any], token: str | None) -> Any:
    from daengs_backend.main import app

    app.dependency_overrides[assistant_router.get_assistant_orchestration_service] = lambda: service
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with TestClient(app) as client:
            return client.post("/assistant/query", json=body, headers=headers)
    finally:
        app.dependency_overrides.clear()


def test_http_thanks_is_answered_through_the_real_endpoint() -> None:
    service, transport, engine, _ = build_service(decision(social_intent="thanks"))
    got = _post(service, {"query": "고마워"}, create_access_token(uuid.uuid4(), SubjectType.APP))
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "ANSWERED"
    assert body["message"] == social_message("thanks")
    assert body["results"] == [] and body["handoffs"] == [] and body["clarify"] is None
    assert len(transport.prompts) == 1 and engine.runs == 0


def test_http_social_text_still_requires_auth() -> None:
    service, transport, engine, _ = build_service(decision(social_intent="thanks"))
    got = _post(service, {"query": "고마워"}, token=None)
    assert got.status_code == 401
    assert transport.prompts == [] and engine.runs == 0
