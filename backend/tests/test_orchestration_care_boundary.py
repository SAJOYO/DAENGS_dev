"""General-care (husbandry) questions have no capability — and routing must not invent one.

Card: PR #172 (`feat/assistant-general-care-routing`). A real user asked
"푸들 산책은 몇 회가 좋아?". That is not current environmental walking suitability
(Walk), not behavior change (Training), and not institutional/procedural evidence
(Life). The evidence-source audit found no executable source for walk frequency,
feeding frequency, sleep, or water-intake norms (routing doc §2 "일반 돌봄"), so
Option C applies: no `care` capability is added.

Measured defect (2026-09-03, two paid calls, production `semantic-router-ko-v4`,
`gemini-3.1-flash-lite`, temperature 0): BOTH "푸들 산책은 몇 회가 좋아?" and
"강아지는 하루에 몇 번 산책해야 해?" came back `{"execute": ["life"]}` — Life's
"official guidance" wording absorbed ordinary care advice, and Life's `/ask` only
abstains on zero hits, so the user would get an OK answer over unrelated ordinance
chunks. `semantic-router-ko-v5` narrowed Life to formal institutional / legal /
administrative / policy / contractual evidence and declared general husbandry
unsupported (empty decision). Its "walk frequency or duration" wording, however,
also suppressed Walk for today's / this evening's walking time-window questions
(frozen mixed_10 · clarify_08 lost Walk; run v6 failed one gate).
`semantic-router-ko-v6` keeps the v5 Life restriction and distinguishes ROUTINE
or normative exercise advice (unsupported) from CURRENT-day timing/suitability
(Walk). Neither version answers care questions.

Every test uses a fake Gemini transport — no provider spend. They pin (a) what the
DETERMINISTIC layer does given the semantically correct decision and (b) the v5
prompt contract text. The live classifier is certified separately: targeted
production probes and one frozen 80-case regression per prompt version
(`runner_v6.py` for v5, `runner_v7.py` for v6), recorded in PR #172 and
docs/orchestration-router-benchmark.md.
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
    PROMPT_VERSION,
    ROUTER_MODEL_ID,
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
    {
        "id": "care_walk_duration_routine",
        "query": "성견은 하루에 몇 분 정도 산책해야 해?",
        "decision": {"execute": [], "handoffs": []},
        "expect": "unsupported",
    },
    {
        "id": "care_puppy_sleep",
        "query": "3개월 강아지는 얼마나 자야 해?",
        "decision": {"execute": [], "handoffs": []},
        "expect": "unsupported",
    },
    {
        "id": "care_feeding_frequency",
        "query": "성견은 하루에 밥을 몇 번 줘?",
        "decision": {"execute": [], "handoffs": []},
        "expect": "unsupported",
    },
    {
        "id": "care_water_intake",
        "query": "강아지는 물을 얼마나 마시는 게 보통이야?",
        "decision": {"execute": [], "handoffs": []},
        "expect": "unsupported",
    },
    # Still husbandry in v1: naming an institution does not make it Life.
    {
        "id": "care_walk_frequency_official_wording",
        "query": "정부 기관에서 권장하는 강아지 하루 산책 횟수는?",
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
    # Today's / this evening's walking time window is Walk (frozen clarify_03 shape),
    # even without naming weather or air quality.
    {
        "id": "walk_evening_time_window",
        "query": "저녁 산책하기 제일 나은 시간 추천해줘",
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
        "id": "life_registration_penalty",
        "query": "반려견 등록 의무를 어기면 과태료가 있어?",
        "decision": {"execute": ["life"], "handoffs": []},
        "expect": "life",
    },
    {
        "id": "life_train_transport_rules",
        "query": "기차에 반려견을 태울 때 규정이 어떻게 돼?",
        "decision": {"execute": ["life"], "handoffs": []},
        "expect": "life",
    },
    # Grooming frequency is husbandry, not an errand — added with the Place
    # destination (D-051) because "목욕" is the first care noun that also names a
    # thing Place can sell. The norm has no destination; the shop does.
    {
        "id": "care_bathing_frequency",
        "query": "목욕은 몇 주마다 해야 해?",
        "decision": {"execute": [], "handoffs": []},
        "expect": "unsupported",
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


UNSUPPORTED_IDS = [c["id"] for c in CARE_BOUNDARY_CASES if c["expect"] == "unsupported"]
LIFE_IDS = [c["id"] for c in CARE_BOUNDARY_CASES if c["expect"] == "life"]


@pytest.mark.parametrize("case_id", UNSUPPORTED_IDS)
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


@pytest.mark.parametrize("case_id", UNSUPPORTED_IDS)
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
    assert {name.value for name in CapabilityName} == {"training", "life", "walk", "place"}
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


@pytest.mark.parametrize("case_id", LIFE_IDS)
async def test_formal_institutional_question_is_life(case_id: str) -> None:
    service, _, adapters, case = _run_case(case_id)
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


# ------------------------------------------------------ v5 prompt contract (pinned)


def _policy() -> str:
    prompt = build_semantic_router_prompt(query="x", context={})
    return prompt.split("USER_QUERY:")[0]


def test_prompt_is_v7_with_the_same_model_and_schema_shape() -> None:
    """v7 (D-051) added `place` and nothing else.

    The care boundary this file defends is a *prompt* boundary, so it pins the prompt
    version. v7 adds one EXECUTE name; the schema's shape, the handoff pair, the
    social_intent field and the model are all unchanged, and the husbandry rules below
    are re-asserted verbatim against the new prompt.
    """
    assert PROMPT_VERSION == "semantic-router-ko-v7"
    assert ROUTER_MODEL_ID == "gemini-3.1-flash-lite"
    assert "PROMPT_VERSION: semantic-router-ko-v7" in _policy()
    # Schema shape unchanged: same three properties, two HANDOFF targets, and exactly
    # one new EXECUTE name appended after the original three.
    schema = SemanticRoutingDecision.model_json_schema()
    assert set(schema["properties"]) == {"execute", "handoffs", "social_intent"}
    execute_enum = schema["properties"]["execute"]["items"]["enum"]
    handoff_enum = schema["properties"]["handoffs"]["items"]["enum"]
    assert execute_enum == ["training", "life", "walk", "place"]
    assert handoff_enum == ["skin", "gait"]


def test_general_care_is_not_absorbed_by_the_new_place_destination() -> None:
    """Adding Place must not give husbandry questions a home (routing §2 option C).

    "목욕은 몇 주마다 해야 해?" is a care-frequency question that happens to name a
    service a Place could sell. The prompt has to separate the norm from the errand,
    or the newest destination quietly becomes the dumping ground the card refused to
    create for `care`.
    """
    policy = _policy()
    assert "is not\nPlace unless the user asks where to go" in policy
    assert "how often to bathe a dog is unsupported, while finding a\ngrooming shop is Place" in policy


def test_v5_life_definition_is_formal_institutional_evidence_only() -> None:
    policy = _policy()
    life_line = next(block for block in policy.split("\n- ") if block.startswith("execute.life:"))
    for topic in (
        "FORMAL",
        "registrations",
        "official procedures",
        "eligibility",
        "fees",
        "deadlines",
        "statutory or regulatory requirements",
        "insurance terms",
        "transport or travel terms",
    ):
        assert topic in life_line, topic
    # "official guidance" is no longer a bare Life trigger.
    assert "Official guidance belongs to Life ONLY when it concerns such formal" in life_line


def test_v6_declares_routine_husbandry_unsupported_without_keyword_lists() -> None:
    policy = " ".join(_policy().split())
    assert "General pet husbandry or care recommendations are NOT supported" in policy
    for example in (
        "routine or normative advice on how often or how long a dog should walk or exercise",
        "independent of current conditions",
        "feeding frequency or amount",
        "sleep duration",
        "water intake",
        "grooming or care norms",
        "breed-, age-, or body-size-specific care",
    ):
        assert example in policy, example
    assert "even when it mentions an institution, an official source, or a recommendation" in policy
    assert "return both lists empty and leave social_intent null" in policy
    # v5's blanket "not Walk merely because it concerns walking" is gone (it suppressed Walk
    # for today's walking window); v6 draws the line at routine vs. current-day instead.
    assert "not Walk merely because it concerns walking" not in policy
    # Semantic rule only: no Korean keyword list and no answer instruction.
    for keyword in ("산책", "급여", "수면", "정부", "권장", "푸들", "저녁", "오늘"):
        assert keyword not in policy
    assert "not an answer generator" in policy


def test_v6_keeps_todays_walking_time_window_in_walk_without_broadening_to_routines() -> None:
    policy = " ".join(_policy().split())
    assert "deciding whether or when to walk now, today, or this evening" in policy
    assert "choosing a suitable walking time window for today" in policy
    assert "IS Walk (current environmental suitability)" in policy
    assert "even when weather or air quality is not named explicitly" in policy
    assert "do not extend Walk to recurring exercise routines" in policy


def test_v5_keeps_the_other_boundaries_verbatim() -> None:
    policy = " ".join(_policy().split())
    assert "execute.training: changing dog behavior or teaching skills." in policy
    assert "execute.walk: current environmental walking suitability." in policy
    assert "Select Walk only for current environmental walking suitability" in policy
    assert "Skin and gait are handoffs only" in policy
    assert "Preserve multi-intent" in policy
    assert "purely social" in policy


def test_acceptance_table_decisions_are_all_schema_valid() -> None:
    for case in CARE_BOUNDARY_CASES:
        assert validate_semantic_decision(json.dumps(case["decision"])) is not None, case["id"]
