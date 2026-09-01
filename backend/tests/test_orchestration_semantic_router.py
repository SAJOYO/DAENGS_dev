"""Production semantic router: Gemini owns destination selection only (Card 2B).

Every test drives the real planning layer with a fake Gemini transport — no
provider spend — and asserts that the deterministic assembler, the O-14 retry
policy, and the untouched Card 1 execution core behave as accepted in D-041.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from daengs_backend.orchestration.contracts import (
    AssistantStatus,
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    LifePayload,
    PrincipalContext,
    RouterKind,
    TrainingPayload,
    WalkPayload,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.planner import assemble_route_plan, resolve_deterministic_route
from daengs_backend.orchestration.semantic import (
    ROUTER_MODEL_ID,
    GeminiSemanticRouter,
    SemanticRoutingDecision,
    SemanticRoutingError,
    build_semantic_router_prompt,
)
from daengs_backend.orchestration.service import AssistantOrchestrationService

PRINCIPAL = PrincipalContext(subject="test-user", kind="APP_USER")
LOCATION = {"location": {"lat": 37.5665, "lon": 126.978}}
QUERY = "우리 개가 산책 중에 짖어요. 어떻게 교육하죠?"

VALID = json.dumps({"execute": ["training"], "handoffs": []})
INVALID = json.dumps({"execute": ["medical"], "handoffs": []})


class ScriptedTransport:
    """Return canned provider outputs in order and record every prompt."""

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


def build_service(
    *outputs: object,
) -> tuple[AssistantOrchestrationService, ScriptedTransport, dict[CapabilityName, FakeAdapter]]:
    transport = ScriptedTransport(*outputs)
    adapters = {name: FakeAdapter(name) for name in CapabilityName}
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine(adapters),
        semantic_router=GeminiSemanticRouter(generate=transport),
    )
    return service, transport, adapters


def decision(execute: list[str] = (), handoffs: list[str] = ()) -> str:
    return json.dumps({"execute": list(execute), "handoffs": list(handoffs)})


# ---------------------------------------------------------------- payload assembly


async def test_training_selection_carries_the_exact_original_question() -> None:
    service, _, adapters = build_service(decision(["training"]))
    response = await service.run(query=QUERY, principal=PRINCIPAL)
    assert response.status == AssistantStatus.ANSWERED
    [request] = adapters[CapabilityName.TRAINING].calls
    assert request.payload == TrainingPayload(question=QUERY)


async def test_life_selection_carries_the_exact_original_question() -> None:
    query = "맹견 책임보험은 의무인가요?"
    service, _, adapters = build_service(decision(["life"]))
    response = await service.run(query=query, principal=PRINCIPAL)
    assert response.status == AssistantStatus.ANSWERED
    [request] = adapters[CapabilityName.LIFE].calls
    assert request.payload == LifePayload(question=query)


async def test_walk_uses_exact_trusted_structured_coordinates() -> None:
    service, transport, adapters = build_service(decision(["walk"]))
    response = await service.run(
        query="지금 산책 가도 괜찮아?", principal=PRINCIPAL, context=dict(LOCATION)
    )
    assert response.status == AssistantStatus.ANSWERED
    [request] = adapters[CapabilityName.WALK].calls
    assert request.payload == WalkPayload(lat=37.5665, lon=126.978)
    # The model never sees coordinates; they cross only the trusted context path.
    assert "37.5665" not in transport.prompts[0]


@pytest.mark.parametrize(
    ("location", "missing"),
    [
        ({"lon": 126.978}, ["location.lat"]),
        ({"lat": 37.5665}, ["location.lon"]),
        ({}, ["location.lat", "location.lon"]),
    ],
)
async def test_walk_with_missing_coordinates_clarifies(location: dict, missing: list[str]) -> None:
    service, _, adapters = build_service(decision(["walk"]))
    response = await service.run(
        query="지금 산책 가도 괜찮아?", principal=PRINCIPAL, context={"location": location}
    )
    assert response.status == AssistantStatus.CLARIFY
    assert response.clarify is not None and response.clarify.missing == missing
    assert all(not adapter.calls for adapter in adapters.values())


async def test_clarify_is_exclusive_over_other_execute_and_handoffs() -> None:
    service, _, adapters = build_service(decision(["training", "walk"], ["skin"]))
    response = await service.run(query="산책 어때? 짖는 것도 봐줘", principal=PRINCIPAL)
    assert response.status == AssistantStatus.CLARIFY
    assert response.results == [] and response.handoffs == []
    assert all(not adapter.calls for adapter in adapters.values())


async def test_natural_language_place_name_cannot_invent_coordinates() -> None:
    service, _, _ = build_service(decision(["walk"]))
    response = await service.run(query="강남역에서 지금 산책 어때?", principal=PRINCIPAL)
    assert response.status == AssistantStatus.CLARIFY
    assert response.clarify is not None
    assert response.clarify.missing == ["location.lat", "location.lon"]


# ---------------------------------------------------------------------- handoffs


@pytest.mark.parametrize(
    ("target", "reason"),
    [("skin", "image_upload_required"), ("gait", "video_upload_required")],
)
async def test_pure_handoff_uses_the_fixed_reason(target: str, reason: str) -> None:
    service, _, adapters = build_service(decision(handoffs=[target]))
    response = await service.run(query="우리 개 피부/걸음 좀 봐줘", principal=PRINCIPAL)
    assert response.status == AssistantStatus.HANDOFF
    assert [(h.target, h.reason) for h in response.handoffs] == [(target, reason)]
    assert all(not adapter.calls for adapter in adapters.values())


async def test_multi_execute_training_and_life() -> None:
    service, _, adapters = build_service(decision(["training", "life"]))
    response = await service.run(query=QUERY, principal=PRINCIPAL)
    assert response.status == AssistantStatus.ANSWERED
    assert [r.capability for r in response.results] == [
        CapabilityName.TRAINING,
        CapabilityName.LIFE,
    ]
    for name in (CapabilityName.TRAINING, CapabilityName.LIFE):
        assert adapters[name].calls[0].payload.question == QUERY


async def test_training_execute_with_gait_handoff() -> None:
    service, _, adapters = build_service(decision(["training"], ["gait"]))
    response = await service.run(query=QUERY, principal=PRINCIPAL)
    assert response.status == AssistantStatus.ANSWERED
    assert [(h.target, h.reason) for h in response.handoffs] == [("gait", "video_upload_required")]
    assert len(adapters[CapabilityName.TRAINING].calls) == 1


async def test_walk_execute_with_skin_handoff() -> None:
    service, _, adapters = build_service(decision(["walk"], ["skin"]))
    response = await service.run(
        query="산책 괜찮은지랑 피부에 난 것도 봐줘", principal=PRINCIPAL, context=dict(LOCATION)
    )
    assert response.status == AssistantStatus.ANSWERED
    assert [(h.target, h.reason) for h in response.handoffs] == [("skin", "image_upload_required")]
    assert len(adapters[CapabilityName.WALK].calls) == 1


async def test_multiple_execute_with_multiple_handoffs() -> None:
    service, _, adapters = build_service(decision(["training", "life", "walk"], ["skin", "gait"]))
    response = await service.run(query=QUERY, principal=PRINCIPAL, context=dict(LOCATION))
    assert response.status == AssistantStatus.ANSWERED
    assert len(response.results) == 3
    assert [h.target for h in response.handoffs] == ["skin", "gait"]
    assert all(len(adapter.calls) == 1 for adapter in adapters.values())


async def test_empty_semantic_decision_preserves_empty_route_plan_behavior() -> None:
    service, _, adapters = build_service(decision())
    response = await service.run(query="오늘 날씨가 좋네요", principal=PRINCIPAL)
    # The existing aggregate behavior for an empty valid RoutePlan is unchanged:
    # FAILED, not an invented CLARIFY.
    assert response.status == AssistantStatus.FAILED
    assert response.clarify is None
    assert all(not adapter.calls for adapter in adapters.values())


# ------------------------------------------------------------------ O-14 policy


async def test_first_schema_invalid_output_is_retried_exactly_once() -> None:
    service, transport, adapters = build_service("this is not json", decision(["training"]))
    response = await service.run(query=QUERY, principal=PRINCIPAL)
    assert response.status == AssistantStatus.ANSWERED
    assert len(transport.prompts) == 2
    assert len(adapters[CapabilityName.TRAINING].calls) == 1


async def test_second_schema_invalid_output_is_a_router_failure() -> None:
    service, transport, adapters = build_service(INVALID, "{broken")
    response = await service.run(query=QUERY, principal=PRINCIPAL)
    assert response.status == AssistantStatus.FAILED
    assert response.clarify is None and response.results == [] and response.handoffs == []
    assert len(transport.prompts) == 2
    assert all(not adapter.calls for adapter in adapters.values())
    # The invalid raw model output is never surfaced to the user.
    assert "medical" not in response.message and "{broken" not in response.message


async def test_schema_valid_misroute_is_not_retried() -> None:
    service, transport, adapters = build_service(decision(["life"]))
    response = await service.run(query=QUERY, principal=PRINCIPAL)
    assert response.status == AssistantStatus.ANSWERED
    assert len(transport.prompts) == 1
    assert len(adapters[CapabilityName.LIFE].calls) == 1


async def test_provider_failure_is_a_router_failure_not_clarify() -> None:
    service, _transport, adapters = build_service(RuntimeError("provider down"))
    response = await service.run(query=QUERY, principal=PRINCIPAL)
    assert response.status == AssistantStatus.FAILED
    assert response.clarify is None
    assert all(not adapter.calls for adapter in adapters.values())


async def test_router_raises_after_two_invalid_attempts() -> None:
    router = GeminiSemanticRouter(generate=ScriptedTransport(INVALID, INVALID))
    with pytest.raises(SemanticRoutingError):
        await router.select(query=QUERY, context={})


# ------------------------------------------------------------ deterministic path


async def test_requested_capability_resolves_without_a_provider_call() -> None:
    service, transport, adapters = build_service()  # any provider call would IndexError
    response = await service.run(query=QUERY, principal=PRINCIPAL, requested_capability="training")
    assert response.status == AssistantStatus.ANSWERED
    assert transport.prompts == []
    assert adapters[CapabilityName.TRAINING].calls[0].payload.question == QUERY


async def test_deterministic_walk_without_coordinates_clarifies() -> None:
    plan = resolve_deterministic_route(requested_capability="walk", query="산책 어때?", context={})
    assert plan is not None and plan.router == RouterKind.DETERMINISTIC
    assert plan.clarify is not None and plan.model is None


async def test_deterministic_handoff_signal_resolves_to_a_handoff() -> None:
    plan = resolve_deterministic_route(
        requested_capability="gait", query="걸음 분석해줘", context={}
    )
    assert plan is not None
    assert [(h.target, h.reason) for h in plan.handoffs] == [("gait", "video_upload_required")]
    assert plan.requests == []


async def test_unresolved_signal_falls_back_to_semantic_routing() -> None:
    service, transport, _ = build_service(decision(["training"]))
    response = await service.run(query=QUERY, principal=PRINCIPAL, requested_capability="place")
    assert response.status == AssistantStatus.ANSWERED
    assert len(transport.prompts) == 1


# ----------------------------------------------------------- plan/observability


async def test_semantic_route_plan_records_llm_router_and_model() -> None:
    plan = assemble_route_plan(
        SemanticRoutingDecision(execute=["training"], handoffs=[]),
        query=QUERY,
        context={},
        router=RouterKind.LLM,
    )
    assert plan.router == RouterKind.LLM
    assert plan.model == ROUTER_MODEL_ID


def test_prompt_carries_only_approved_routing_metadata() -> None:
    prompt = build_semantic_router_prompt(
        query=QUERY,
        context={
            "location": {"lat": 37.5665, "lon": 126.978},
            "source": "assistant",
            "active_dog_id": "dog-1",
            "note": "unapproved",
        },
    )
    assert QUERY in prompt and "semantic-router-ko-v3" in prompt
    metadata_line = next(
        line for line in prompt.splitlines() if line.startswith("ROUTING_METADATA:")
    )
    assert json.loads(metadata_line[len("ROUTING_METADATA:") :]) == {
        "source": "assistant",
        "active_dog_id": "dog-1",
    }


def test_importing_the_planning_layer_stays_light() -> None:
    """Importing the router/planner must not pull ML, Training, or the provider SDK."""
    probe = (
        "import json, sys;"
        " import daengs_backend.orchestration.service;"
        " import daengs_backend.orchestration.semantic;"
        ' print("MODULES=" + json.dumps(sorted(sys.modules)))'
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr[-2000:]
    line = next(ln for ln in done.stdout.splitlines() if ln.startswith("MODULES="))
    modules = set(json.loads(line[len("MODULES=") :]))
    forbidden = ("torch", "sentence_transformers", "transformers", "daengs_training", "google")
    loaded = sorted(m for m in modules if any(m == f or m.startswith(f + ".") for f in forbidden))
    assert not loaded, loaded
