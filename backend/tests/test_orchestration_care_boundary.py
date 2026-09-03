"""General-care (husbandry) questions have no capability — and routing must not invent one.

Card: PR #172 (`feat/assistant-general-care-routing`). A real user asked
"푸들 산책은 몇 회가 좋아?". That is not current environmental walking suitability
(Walk), not behavior change (Training), and not institutional/procedural evidence
(Life). The evidence-source audit found no executable source for walk frequency,
feeding frequency, sleep, or water-intake norms (routing doc §2 "일반 돌봄"), so
Option C applies: the gap is recorded here and in docs, no `care` capability is
added, and the deterministic layer keeps the truthful unsupported behavior.

Every test uses a fake Gemini transport — no provider spend. The tests pin what
the DETERMINISTIC layer does given the semantically correct destination decision;
they cannot certify the live classifier (that is the frozen 80-case benchmark's
job, and a paid probe of these cases is a separate human-approved step).
"""

from __future__ import annotations

import json

import pytest

from daengs_backend.orchestration.aggregate import aggregate_results
from daengs_backend.orchestration.contracts import (
    AssistantStatus,
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    PrincipalContext,
    RoutePlan,
    RouterKind,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.planner import assemble_route_plan, resolve_deterministic_route
from daengs_backend.orchestration.semantic import (
    GeminiSemanticRouter,
    SemanticRoutingDecision,
    build_semantic_router_prompt,
    validate_semantic_decision,
)
from daengs_backend.orchestration.service import AssistantOrchestrationService
from daengs_backend.orchestration.social import social_message

PRINCIPAL = PrincipalContext(subject="test-user", kind="APP_USER")
LOCATION = {"location": {"lat": 37.5665, "lon": 126.978}}
UNSUPPORTED_MESSAGE = "실행하거나 안내할 수 있는 기능이 없습니다."

# Routing acceptance cases for this card. `decision` is the semantically correct
# destination selection (what the classifier SHOULD return); the tests below feed
# it through the real planner/engine and assert the deterministic outcome. Reuse
# this table verbatim if a paid live probe of the production router is approved.
CARE_BOUNDARY_CASES: list[dict] = [
    # A–B: general care / husbandry — no supported destination today.
    {
        "id": "care_walk_frequency_breed",
        "query": "푸들 산책은 몇 회가 좋아?",
        "decision": {"execute": [], "handoffs": []},
        "expect": "unsupported",
    },
    {
        "id": "care_walk_frequency",
        "query": "강아지는 하루에 몇 번 산책해야 해?",
        "decision": {"execute": [], "handoffs": []},
        "expect": "unsupported",
    },
    # Existing boundaries stay exactly where they are.
    {
        "id": "walk_air_quality_now",
        "query": "오늘 미세먼지 심한데 산책 나가도 돼?",
        "decision": {"execute": ["walk"], "handoffs": []},
        "expect": "walk",
    },
    {
        "id": "training_leash_pulling",
        "query": "산책할 때 자꾸 당겨. 어떻게 가르쳐?",
        "decision": {"execute": ["training"], "handoffs": []},
        "expect": "training",
    },
    {
        "id": "life_registration_where",
        "query": "반려견 등록은 어디서 해?",
        "decision": {"execute": ["life"], "handoffs": []},
        "expect": "life",
    },
    {
        "id": "social_thanks",
        "query": "고마워",
        "decision": {"execute": [], "handoffs": [], "social_intent": "thanks"},
        "expect": "social",
    },
    {
        "id": "social_then_walk",
        "query": "고마워. 오늘 비 오는데 산책 나가도 돼?",
        "decision": {"execute": ["walk"], "handoffs": []},
        "expect": "walk",
    },
]
_BY_ID = {case["id"]: case for case in CARE_BOUNDARY_CASES}


class ScriptedTransport:
    def __init__(self, *outputs: object) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> object:
        self.prompts.append(prompt)
        return self.outputs.pop(0)


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


def _run_case(case_id: str):
    case = _BY_ID[case_id]
    service, transport, adapters = build_service(json.dumps(case["decision"]))
    return service, transport, adapters, case


# ------------------------------------------------- A–B: care questions stay unsupported


@pytest.mark.parametrize("case_id", ["care_walk_frequency_breed", "care_walk_frequency"])
async def test_care_question_with_empty_decision_is_truthfully_unsupported(case_id: str) -> None:
    service, transport, adapters, case = _run_case(case_id)
    response = await service.run(query=case["query"], principal=PRINCIPAL, context=dict(LOCATION))
    # Not Walk: no coordinate CLARIFY and no Walk adapter call, even though the
    # word "산책" is present and trusted coordinates are available.
    assert response.status == AssistantStatus.FAILED
    assert response.message == UNSUPPORTED_MESSAGE
    assert response.clarify is None and response.results == [] and response.handoffs == []
    assert all(not adapter.calls for adapter in adapters.values())
    assert len(transport.prompts) == 1  # empty is schema-valid: no O-14 retry


@pytest.mark.parametrize("case_id", ["care_walk_frequency_breed", "care_walk_frequency"])
def test_planner_does_not_promote_a_care_question_by_keyword(case_id: str) -> None:
    """The deterministic assembler adds nothing the decision did not select."""
    case = _BY_ID[case_id]
    plan = assemble_route_plan(
        SemanticRoutingDecision(),
        query=case["query"],
        context=dict(LOCATION),
        router=RouterKind.LLM,
    )
    assert plan.requests == [] and plan.handoffs == [] and plan.clarify is None
    # And there is no deterministic signal that could route it either.
    assert (
        resolve_deterministic_route(
            requested_capability=None, query=case["query"], context=dict(LOCATION)
        )
        is None
    )


def test_care_question_is_not_hard_routed_by_the_prompt_builder() -> None:
    """The prompt carries the query verbatim; no keyword pre-classification is injected."""
    prompt = build_semantic_router_prompt(
        query=_BY_ID["care_walk_frequency_breed"]["query"], context={}
    )
    assert "ROUTING_METADATA: {}" in prompt
    assert prompt.rstrip().endswith("USER_QUERY: 푸들 산책은 몇 회가 좋아?")


# ------------------------------------------------ no fake `care` capability exists


def test_no_care_capability_exists_in_the_contracts() -> None:
    assert {name.value for name in CapabilityName} == {"training", "life", "walk"}
    for invented in ("care", "husbandry", "general", "nutrition"):
        assert invented not in {name.value for name in CapabilityName}


@pytest.mark.parametrize("invented", ["care", "husbandry", "general"])
def test_router_output_naming_an_invented_capability_is_schema_invalid(invented: str) -> None:
    assert validate_semantic_decision(json.dumps({"execute": [invented], "handoffs": []})) is None
    assert validate_semantic_decision(json.dumps({"execute": [], "handoffs": [invented]})) is None


async def test_invented_capability_output_takes_the_existing_o14_path() -> None:
    """Schema-invalid → one retry; still invalid → FAILED, nothing executes, nothing surfaces."""
    service, transport, adapters = build_service(
        json.dumps({"execute": ["care"], "handoffs": []}),
        json.dumps({"execute": ["care"], "handoffs": []}),
    )
    response = await service.run(query="푸들 산책은 몇 회가 좋아?", principal=PRINCIPAL)
    assert response.status == AssistantStatus.FAILED
    assert len(transport.prompts) == 2
    assert all(not adapter.calls for adapter in adapters.values())
    assert "care" not in response.message


def test_route_plan_rejects_an_invented_capability_request() -> None:
    with pytest.raises(ValueError):
        RoutePlan.model_validate(
            {
                "requests": [
                    {"capability": "care", "payload": {"question": "x"}, "timeout_ms": None}
                ],
                "handoffs": [],
                "clarify": None,
                "router": "llm",
                "model": None,
            }
        )


def test_requested_capability_care_is_not_a_deterministic_route() -> None:
    """`requested_capability` is a hint, not authorization, and cannot name a non-capability."""
    assert (
        resolve_deterministic_route(
            requested_capability="care", query="푸들 산책은 몇 회가 좋아?", context={}
        )
        is None
    )


def test_empty_route_plan_aggregates_to_the_unsupported_message() -> None:
    plan = RoutePlan(requests=[], handoffs=[], clarify=None, router=RouterKind.LLM, model=None)
    response = aggregate_results(request_id="r", route_plan=plan, results=[])
    assert response.status == AssistantStatus.FAILED and response.message == UNSUPPORTED_MESSAGE


# ------------------------------------------ existing boundaries are unchanged


async def test_current_air_quality_question_is_walk_with_coordinates() -> None:
    service, _, adapters, case = _run_case("walk_air_quality_now")
    response = await service.run(query=case["query"], principal=PRINCIPAL, context=dict(LOCATION))
    assert response.status == AssistantStatus.ANSWERED
    assert [r.capability for r in response.results] == [CapabilityName.WALK]
    assert len(adapters[CapabilityName.WALK].calls) == 1


async def test_current_air_quality_question_clarifies_without_coordinates() -> None:
    service, _, adapters, case = _run_case("walk_air_quality_now")
    response = await service.run(query=case["query"], principal=PRINCIPAL)
    assert response.status == AssistantStatus.CLARIFY
    assert response.clarify is not None
    assert response.clarify.missing == ["location.lat", "location.lon"]
    assert all(not adapter.calls for adapter in adapters.values())


async def test_leash_pulling_question_is_training_not_walk() -> None:
    service, _, adapters, case = _run_case("training_leash_pulling")
    response = await service.run(query=case["query"], principal=PRINCIPAL, context=dict(LOCATION))
    assert response.status == AssistantStatus.ANSWERED
    [request] = adapters[CapabilityName.TRAINING].calls
    assert request.payload.question == case["query"]
    assert not adapters[CapabilityName.WALK].calls


async def test_registration_question_is_life() -> None:
    service, _, adapters, case = _run_case("life_registration_where")
    response = await service.run(query=case["query"], principal=PRINCIPAL)
    assert response.status == AssistantStatus.ANSWERED
    [request] = adapters[CapabilityName.LIFE].calls
    assert request.payload.question == case["query"]


async def test_pure_thanks_is_the_social_template() -> None:
    service, _, adapters, case = _run_case("social_thanks")
    response = await service.run(query=case["query"], principal=PRINCIPAL)
    assert response.status == AssistantStatus.ANSWERED
    assert response.message == social_message("thanks")
    assert response.results == [] and response.handoffs == [] and response.clarify is None
    assert all(not adapter.calls for adapter in adapters.values())


async def test_thanks_followed_by_current_rain_question_is_walk() -> None:
    service, _, adapters, case = _run_case("social_then_walk")
    response = await service.run(query=case["query"], principal=PRINCIPAL)
    # Capability intent wins over the greeting: Walk, and CLARIFY without coordinates.
    assert response.status == AssistantStatus.CLARIFY
    assert response.message != social_message("thanks")
    assert all(not adapter.calls for adapter in adapters.values())

    service, _, adapters, case = _run_case("social_then_walk")
    response = await service.run(query=case["query"], principal=PRINCIPAL, context=dict(LOCATION))
    assert response.status == AssistantStatus.ANSWERED
    assert len(adapters[CapabilityName.WALK].calls) == 1


def test_acceptance_table_decisions_are_all_schema_valid() -> None:
    for case in CARE_BOUNDARY_CASES:
        assert validate_semantic_decision(json.dumps(case["decision"])) is not None, case["id"]
