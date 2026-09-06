"""일반 답변 폴백 (#279) — 결정론 부분만 잰다. 프로바이더 호출 0.

폴백은 라우터의 목적지가 아니라 **planner 의 규칙**이다: 라우터가 전문 능력을 하나도 안
골랐고 핸드오프도 없으면, 플래그(`DAENGS_GENERAL_FALLBACK`)가 켜져 있을 때만 `general`
요청 하나를 조립한다. 이 파일이 고정하는 것:

- 플래그가 꺼져 있으면(기본) 지금과 글자까지 같다 — 빈 결정은 FAILED.
- 켜져 있으면 빈 결정 → `general` 하나, payload 는 Life 와 같은 규칙(원문 + 신뢰된 dog).
- 전문 능력이나 핸드오프가 하나라도 있으면 `general` 이 **절대** 안 붙는다.
- 좌표 게이트(CLARIFY)가 폴백보다 먼저다. 명시 신호는 영향이 없고 `general` 은 신호가 아니다.
- 라우터 스키마와 프롬프트는 `general` 을 모른다 — 모델이 고르게 하면 근거 있는 답을 근거
  없는 답으로 바꾸는 오선택이 생기고, 그 방향의 실수가 가장 나쁘다.
- 어댑터는 answer/refuse/프로바이더 실패를 OK/REFUSED/ERROR 로 옮기고, 거절 문구는 모델이
  아니라 코드가 쓴다.

두 구현(LangGraph · 에이전트)의 동치는 `test_orchestrator_failure_contract.py` 가 잰다.
"""

from __future__ import annotations

import json
from typing import get_args

import pytest

from daengs_backend.config import settings
from daengs_backend.orchestration.adapters.general import (
    GENERAL_MODEL_ID,
    GENERAL_PROMPT_VERSION,
    GeneralCapabilityAdapter,
    build_general_prompt,
    validate_general_answer,
)
from daengs_backend.orchestration.aggregate import aggregate_results
from daengs_backend.orchestration.contracts import (
    AssistantStatus,
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    DogContext,
    GeneralPayload,
    LifePayload,
    PrincipalContext,
    RoutePlan,
    RouterKind,
    TrainingPayload,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.planner import (
    _payload_for,
    assemble_route_plan,
    resolve_deterministic_route,
)
from daengs_backend.orchestration.semantic import (
    ROUTER_MODEL_ID,
    ExecuteName,
    GeminiSemanticRouter,
    SemanticRoutingDecision,
    build_semantic_router_prompt,
    validate_semantic_decision,
)
from daengs_backend.orchestration.service import AssistantOrchestrationService
from daengs_backend.orchestration.social import social_message
from tools.router_benchmark.evaluate import ALLOWED_EXECUTE, evaluate_benchmark
from tools.router_benchmark.schemas import load_gold_v3_cases

PRINCIPAL = PrincipalContext(subject="test-user", kind="APP_USER")
SEOUL = {"location": {"lat": 37.5, "lon": 127.0}}
DOG = {"dog": {"breed": "푸들", "age_months": 30}}
QUERY = "강아지 발톱은 얼마나 자주 깎아야 해?"
EMPTY = SemanticRoutingDecision(execute=[], handoffs=[])


@pytest.fixture
def fallback_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "general_fallback", True)


def plan(decision: SemanticRoutingDecision, *, context: dict, general_fallback: bool) -> RoutePlan:
    return assemble_route_plan(
        decision,
        query=QUERY,
        context=context,
        router=RouterKind.LLM,
        general_fallback=general_fallback,
    )


# ── planner: 규칙 자체 ─────────────────────────────────────────────────


def test_flag_off_is_the_default_and_an_empty_decision_stays_an_empty_plan() -> None:
    assert settings.general_fallback is False
    built = assemble_route_plan(EMPTY, query=QUERY, context=dict(SEOUL), router=RouterKind.LLM)
    assert built.requests == [] and built.handoffs == [] and built.clarify is None


def test_flag_on_empty_decision_assembles_exactly_one_general_request() -> None:
    built = plan(EMPTY, context={**SEOUL, **DOG}, general_fallback=True)
    [request] = built.requests
    assert request.capability == CapabilityName.GENERAL
    assert request.payload == GeneralPayload(
        question=QUERY, dog=DogContext(breed="푸들", age_months=30)
    )
    assert request.timeout_ms is None
    assert built.handoffs == [] and built.clarify is None
    # 좌표가 있어도 payload 로 건너가지 않는다 — 폴백은 Walk·Place 의 질문에 답하지 않는다.
    assert set(request.payload.model_dump()) == {"question", "dog"}


def test_general_payload_follows_the_life_rule_exactly() -> None:
    with_dog = _payload_for("general", query="  원문 그대로 ", context={**SEOUL, **DOG})
    assert with_dog == {"question": "  원문 그대로 ", "dog": {"breed": "푸들", "age_months": 30}}
    assert _payload_for("general", query=QUERY, context=dict(SEOUL)) == {"question": QUERY}
    # 잘못된 dog 항목은 Life 와 똑같이 버린다 — 실패가 아니라 "프로필 없음" 이다.
    assert _payload_for("general", query=QUERY, context={"dog": {"breed": "  "}}) == {
        "question": QUERY
    }
    life = _payload_for("life", query=QUERY, context={**SEOUL, **DOG})
    assert _payload_for("general", query=QUERY, context={**SEOUL, **DOG}) == life


@pytest.mark.parametrize(
    "decision",
    [
        SemanticRoutingDecision(execute=["training"]),
        SemanticRoutingDecision(execute=["walk"]),
        SemanticRoutingDecision(execute=["training", "life", "walk", "place"]),
        SemanticRoutingDecision(handoffs=["skin"]),
        SemanticRoutingDecision(execute=["life"], handoffs=["gait"]),
    ],
    ids=["training", "walk", "all-four", "handoff-only", "execute+handoff"],
)
def test_any_specialized_selection_never_gains_general(decision: SemanticRoutingDecision) -> None:
    built = plan(decision, context=dict(SEOUL), general_fallback=True)
    assert CapabilityName.GENERAL not in {request.capability for request in built.requests}
    assert [request.capability.value for request in built.requests] == sorted(
        decision.execute, key=["training", "life", "walk", "place"].index
    )
    assert [handoff.target for handoff in built.handoffs] == decision.handoffs


def test_coordinate_gate_still_wins_over_the_fallback() -> None:
    """CLARIFY 는 배타 (O-8). 좌표 없는 산책·장소 선택은 폴백이 아니라 되묻기다."""
    for execute in (["walk"], ["place"], ["walk", "place"]):
        built = plan(SemanticRoutingDecision(execute=execute), context={}, general_fallback=True)
        assert built.clarify is not None and built.requests == []


@pytest.mark.parametrize(
    ("execute", "expected"),
    [
        (["general", "walk"], ["walk", "general"]),
        (["general", "training"], ["training", "general"]),
        (["general", "place", "life"], ["life", "place", "general"]),
    ],
)
def test_mixed_decision_keeps_general_and_orders_it_last_when_flag_is_on(
    execute: list[str], expected: list[str]
) -> None:
    """D-056 ①: 라우터가 전문 능력에 **더해** 고른 `general` 은 살아남고 맨 뒤에 선다."""
    built = plan(
        SemanticRoutingDecision(execute=execute), context=dict(SEOUL), general_fallback=True
    )
    assert [request.capability.value for request in built.requests] == expected
    general = built.requests[-1]
    assert general.payload == GeneralPayload(question=QUERY)


@pytest.mark.parametrize("execute", [["general", "walk"], ["general"], ["training", "general"]])
def test_flag_off_strips_general_from_the_decision(execute: list[str]) -> None:
    """플래그가 꺼져 있으면 라우터가 `general` 을 골라도 예전 계획 그대로다 — `general` 단독은 빈 계획."""
    built = plan(
        SemanticRoutingDecision(execute=execute), context=dict(SEOUL), general_fallback=False
    )
    assert [request.capability.value for request in built.requests] == [
        name for name in execute if name != "general"
    ]


def test_general_never_needs_coordinates() -> None:
    built = plan(SemanticRoutingDecision(execute=["general"]), context={}, general_fallback=True)
    assert built.clarify is None
    assert [request.capability.value for request in built.requests] == ["general"]
    # ...but a coordinate capability beside it still gates the whole selection (O-8).
    gated = plan(
        SemanticRoutingDecision(execute=["general", "walk"]), context={}, general_fallback=True
    )
    assert gated.clarify is not None and gated.requests == []


def test_general_is_not_a_resolvable_explicit_signal(fallback_on: None) -> None:
    """`requested_capability="general"` 은 모르는 신호라 의미 라우팅으로 넘어간다 (불변식 12)."""
    assert (
        resolve_deterministic_route(
            requested_capability="general", query=QUERY, context=dict(SEOUL)
        )
        is None
    )
    # 다른 명시 신호는 플래그와 무관하게 예전 그대로다.
    training = resolve_deterministic_route(
        requested_capability="training", query=QUERY, context=dict(SEOUL)
    )
    assert training is not None
    assert [request.capability for request in training.requests] == [CapabilityName.TRAINING]


def test_router_schema_and_prompt_offer_general_as_an_additive_destination() -> None:
    """D-056 ①: v9 부터 모델이 `general` 을 고를 수 있다 — 전문 능력에 더해서, 대신해서는 아니다."""
    assert get_args(ExecuteName)[-1] == "general"
    assert (
        validate_semantic_decision(json.dumps({"execute": ["general"], "handoffs": []})) is not None
    )
    assert validate_semantic_decision(json.dumps({"execute": ["walk", "general"], "handoffs": []}))
    prompt = build_semantic_router_prompt(query="x", context={})
    assert "execute.general:" in prompt
    assert "IN ADDITION to any specialized destination" in prompt
    assert "never replaces Training, Life, Walk, or Place" in prompt
    assert "not about dogs at all" in prompt and "selects NOTHING" in prompt


# ── service: LangGraph 경로 ────────────────────────────────────────────


class FakeAdapter:
    def __init__(self, capability: CapabilityName, result: CapabilityResult | Exception) -> None:
        self.capability = capability
        self._result = result
        self.calls: list[CapabilityRequest] = []

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        self.calls.append(request)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def ok(capability: CapabilityName, answer: str) -> CapabilityResult:
    return CapabilityResult(
        capability=capability, status=CapabilityStatus.OK, data={"answer": answer}, elapsed_ms=1
    )


class ScriptedTransport:
    def __init__(self, *outputs: object) -> None:
        self.outputs = list(outputs)

    async def __call__(self, prompt: str) -> object:
        return self.outputs.pop(0)


def service(decision: dict, general: CapabilityResult | Exception):
    fakes = {
        CapabilityName.TRAINING: FakeAdapter(
            CapabilityName.TRAINING, ok(CapabilityName.TRAINING, "훈련 답")
        ),
        CapabilityName.GENERAL: FakeAdapter(CapabilityName.GENERAL, general),
    }
    orchestrator = AssistantOrchestrationService(
        engine=OrchestrationEngine(fakes),
        semantic_router=GeminiSemanticRouter(generate=ScriptedTransport(json.dumps(decision))),
    )
    return orchestrator, fakes


async def test_flag_off_empty_decision_is_failed_as_before() -> None:
    orchestrator, fakes = service({"execute": [], "handoffs": []}, ok(CapabilityName.GENERAL, "x"))
    response = await orchestrator.run(query=QUERY, principal=PRINCIPAL, context=dict(SEOUL))
    assert response.status == AssistantStatus.FAILED
    assert response.message == "실행하거나 안내할 수 있는 기능이 없습니다."
    assert all(fake.calls == [] for fake in fakes.values())


async def test_flag_on_empty_decision_runs_general_once_with_the_trusted_payload(
    fallback_on: None,
) -> None:
    orchestrator, fakes = service(
        {"execute": [], "handoffs": []}, ok(CapabilityName.GENERAL, "보통 2~4주에 한 번이면 돼요.")
    )
    response = await orchestrator.run(
        query=QUERY, principal=PRINCIPAL, context={**SEOUL, **DOG}, include_route_trace=True
    )
    assert response.status == AssistantStatus.ANSWERED
    assert response.message == "보통 2~4주에 한 번이면 돼요."
    assert [r.capability for r in response.results] == [CapabilityName.GENERAL]
    [request] = fakes[CapabilityName.GENERAL].calls
    assert request.payload == GeneralPayload(
        question=QUERY, dog=DogContext(breed="푸들", age_months=30)
    )
    assert fakes[CapabilityName.TRAINING].calls == []
    # 경위는 여전히 라우터의 것이다 — 폴백은 라우터 뒤의 규칙이라 새 router 종류가 아니다.
    assert response.route is not None and response.route.router == RouterKind.LLM
    assert response.route.model == ROUTER_MODEL_ID


async def test_flag_on_specialized_decision_does_not_touch_general(fallback_on: None) -> None:
    orchestrator, fakes = service(
        {"execute": ["training"], "handoffs": []}, ok(CapabilityName.GENERAL, "x")
    )
    response = await orchestrator.run(query=QUERY, principal=PRINCIPAL, context=dict(SEOUL))
    assert response.status == AssistantStatus.ANSWERED
    assert [r.capability for r in response.results] == [CapabilityName.TRAINING]
    assert fakes[CapabilityName.GENERAL].calls == []


async def test_flag_on_social_intent_still_answers_with_the_template(fallback_on: None) -> None:
    """스몰토크는 RoutePlan 이전에 끝난다 — 폴백은 조립 단계의 규칙이라 닿지 않는다."""
    orchestrator, fakes = service(
        {"execute": [], "handoffs": [], "social_intent": "greeting"},
        ok(CapabilityName.GENERAL, "x"),
    )
    response = await orchestrator.run(query="안녕!", principal=PRINCIPAL, context=dict(SEOUL))
    assert response.status == AssistantStatus.ANSWERED
    assert response.message == social_message("greeting")
    assert all(fake.calls == [] for fake in fakes.values())


async def test_flag_on_general_refusal_is_a_refused_answer(fallback_on: None) -> None:
    refused = CapabilityResult(
        capability=CapabilityName.GENERAL,
        status=CapabilityStatus.REFUSED,
        refusal={"code": "medication", "message": "수의사에게 확인해 주세요."},
        elapsed_ms=1,
    )
    orchestrator, _ = service({"execute": [], "handoffs": []}, refused)
    response = await orchestrator.run(query=QUERY, principal=PRINCIPAL, context=dict(SEOUL))
    assert response.status == AssistantStatus.REFUSED
    assert response.message == "수의사에게 확인해 주세요."


def test_default_engine_registers_the_general_adapter() -> None:
    assert set(OrchestrationEngine()._adapters) == set(CapabilityName)


# ── adapter: 모델 출력 → CapabilityResult ─────────────────────────────


def general_request(context: dict | None = None) -> CapabilityRequest:
    return CapabilityRequest(
        capability="general",
        payload=_payload_for("general", query=QUERY, context=context or {}),
    )


class Transport:
    def __init__(self, output: object) -> None:
        self.output = output
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> object:
        self.prompts.append(prompt)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


async def run_adapter(output: object, context: dict | None = None):
    transport = Transport(output)
    result = await GeneralCapabilityAdapter(generate=transport).run(
        general_request(context), request_id="r1"
    )
    return result, transport


async def test_adapter_maps_an_answer_to_ok() -> None:
    result, transport = await run_adapter(
        json.dumps({"kind": "answer", "text": "  보통 2~4주에 한 번이면 돼요. ", "reason": None})
    )
    assert result.status == CapabilityStatus.OK
    assert result.data == {"answer": "보통 2~4주에 한 번이면 돼요."}
    assert result.capability == CapabilityName.GENERAL and result.elapsed_ms >= 0
    assert len(transport.prompts) == 1


@pytest.mark.parametrize(
    ("reason", "fragment"),
    [
        ("diagnosis", "동물병원"),
        ("medication", "수의사"),
        ("emergency", "지금 바로 동물병원"),
        ("institutional", "제도 정보 기능"),
        ("off_topic", "반려견에 관한 질문"),
    ],
)
async def test_adapter_maps_a_refusal_to_refused_with_a_fixed_redirect(
    reason: str, fragment: str
) -> None:
    """거절 문구는 코드가 쓴다. 모델의 text 는 REFUSED 에서 사용자에게 가지 않는다."""
    result, _ = await run_adapter(
        {"kind": "refuse", "text": "모델이 지은 거절 문장", "reason": reason}
    )
    assert result.status == CapabilityStatus.REFUSED
    assert result.refusal is not None
    assert result.refusal.code == reason
    assert fragment in result.refusal.message
    assert "모델이 지은" not in result.refusal.message
    assert result.data is None


async def test_adapter_contains_a_provider_failure_as_error() -> None:
    result, _ = await run_adapter(RuntimeError("provider down"))
    assert result.status == CapabilityStatus.ERROR
    assert result.error is not None and result.error.kind == "general_provider_failure"
    assert "provider down" not in result.error.detail


async def test_adapter_maps_a_timeout_to_timeout() -> None:
    result, _ = await run_adapter(TimeoutError())
    assert result.status == CapabilityStatus.TIMEOUT
    assert result.error is not None and result.error.kind == "general_timeout"


@pytest.mark.parametrize(
    "raw",
    [
        "{broken",
        json.dumps({"kind": "answer", "text": "", "reason": None}),
        json.dumps({"kind": "refuse", "text": "", "reason": None}),
        json.dumps({"kind": "refuse", "text": "", "reason": "made_up"}),
        json.dumps({"kind": "answer", "text": "x", "reason": "diagnosis"}),
        json.dumps({"kind": "answer", "text": "x", "reason": None, "extra": 1}),
    ],
    ids=[
        "not-json",
        "empty-answer",
        "refuse-no-reason",
        "unknown-reason",
        "answer+reason",
        "extra",
    ],
)
async def test_adapter_rejects_malformed_output_as_error_without_surfacing_it(raw: str) -> None:
    assert validate_general_answer(raw) is None
    result, _ = await run_adapter(raw)
    assert result.status == CapabilityStatus.ERROR
    assert result.error is not None and result.error.kind == "general_invalid_output"
    assert "made_up" not in result.error.detail and "{broken" not in result.error.detail


async def test_adapter_refuses_a_foreign_payload() -> None:
    request = CapabilityRequest(capability="training", payload=TrainingPayload(question="q"))
    result = await GeneralCapabilityAdapter(generate=Transport("unused")).run(
        request, request_id="r1"
    )
    assert result.status == CapabilityStatus.ERROR
    assert result.error is not None and result.error.kind == "invalid_payload"


def test_general_prompt_carries_the_question_and_dog_but_never_coordinates() -> None:
    payload = GeneralPayload(question=QUERY, dog=DogContext(breed="푸들", age_months=30))
    prompt = build_general_prompt(payload)
    assert f"PROMPT_VERSION: {GENERAL_PROMPT_VERSION}" in prompt
    assert prompt.rstrip().endswith(f"USER_QUERY: {QUERY}")
    assert 'DOG_CONTEXT: {"age_months": 30, "breed": "푸들"}' in prompt
    assert "37.5" not in prompt and "lat" not in prompt.split("GENERAL_ANSWER_JSON_SCHEMA")[0]
    # 안전 경계가 프롬프트에 실제로 있다 — 진단 · 약/용량 · 응급 · 제도/수치 · 도메인 밖.
    for word in ("diagnosis", "medication", "emergency", "institutional", "off_topic"):
        assert word in prompt
    assert "동물병원" in prompt and "제도 정보 기능" in prompt
    assert build_general_prompt(GeneralPayload(question=QUERY)).count("DOG_CONTEXT: {}") == 1


def test_safety_prompt_v2_answers_husbandry_norms_and_narrows_the_refusals() -> None:
    """D-056 ③ⓐ: v1 refused feeding-amount / water-intake norms as institutional or
    diagnosis (#277: 7 of 15 general_care). v2 names those norms answerable with a hedge,
    makes institutional document-backed facts only, and diagnosis explicit requests only."""
    assert GENERAL_PROMPT_VERSION == "general-answer-ko-v2"
    prompt = build_general_prompt(GeneralPayload(question=QUERY))
    # husbandry norms are answerable, with the individual-variation hedge and the authority
    assert "통상 돌봄 기준은 답합니다" in prompt
    for topic in ("급여 횟수", "음수량", "목욕", "빗질", "발톱", "준비물", "사회화 시기", "수면"):
        assert topic in prompt, topic
    assert "개체차가 크다는 단서" in prompt
    assert "사료 포장의 급여표나 수의사가 기준" in prompt
    assert "통상 기준은 institutional 이 아닙니다" in prompt
    # "is this normal" answers with the range and a vet hedge — it is not a diagnosis
    assert "정상인가" in prompt and "거절하지 않습니다" in prompt
    assert '"괜찮은가 / 정상인가" 는 여기가 아닙니다' in prompt
    # institutional = document-backed facts only; diagnosis = explicit requests only
    assert "출처 문서가 있어야 답이 되는 사실" in prompt
    assert "통상 돌봄 기준의 수치는 여기에 넣지 않습니다" in prompt
    assert "병명을 대 달라거나" in prompt and "검사 결과를 해석해 달라고 명시적으로" in prompt
    # medication · emergency · off_topic unchanged
    assert "약 · 영양제 · 용량 · 투여 방법" in prompt
    assert '"지금 바로 동물병원으로" 이상의 처치는 말하지 않습니다' in prompt
    assert "off_topic: 반려견과 무관한 질문." in prompt


def test_general_adapter_uses_the_router_model() -> None:
    assert GENERAL_MODEL_ID == ROUTER_MODEL_ID


# ── aggregate · evaluator · agent prompt ───────────────────────────────


def test_aggregate_has_a_label_for_general() -> None:
    """단독으로만 조립되지만, 라벨이 없으면 다중 결과에서 KeyError 다."""
    route_plan = RoutePlan(requests=[], handoffs=[], router=RouterKind.LLM)
    response = aggregate_results(
        request_id="r1",
        route_plan=route_plan,
        results=[ok(CapabilityName.TRAINING, "훈련 답"), ok(CapabilityName.GENERAL, "일반 답")],
    )
    assert "[일반]\n일반 답" in response.message and "[훈련]\n훈련 답" in response.message


def test_frozen_evaluator_scores_general_as_a_precision_miss_not_an_invented_capability() -> None:
    assert "general" in ALLOWED_EXECUTE
    case = next(c for c in load_gold_v3_cases() if c.case_id == "training_01")
    padded = {
        "requests": [
            {"capability": "training", "payload": {"question": case.query}, "timeout_ms": None},
            {"capability": "general", "payload": {"question": case.query}, "timeout_ms": None},
        ],
        "handoffs": [],
        "clarify": None,
        "router": "llm",
        "model": ROUTER_MODEL_ID,
    }
    result = evaluate_benchmark([case], {case.case_id: [padded]})
    assert result.summary.invented_unsupported_capability_count == 0
    assert result.summary.executable_precision < 1.0
    assert result.summary.executable_recall == 1.0


def test_agent_prompt_mirrors_the_router_boundary_and_hands_off_to_the_fallback() -> None:
    """D-055 ⑦ 규칙 1: 라우터 경계를 바꾸면 같은 PR 에서 에이전트 프롬프트도 바꾼다."""
    pytest.importorskip("langchain")
    from daengs_backend.orchestration.agent.service import _SYSTEM_PROMPT
    from daengs_backend.orchestration.semantic import _POLICY

    assert "explicitly excludes a topic" in _POLICY
    assert "빠진 쪽 도구는 부르지 않습니다" in _SYSTEM_PROMPT
    assert "일반 답변은 시스템이 붙입니다" in _SYSTEM_PROMPT
    assert "답할 수 없다고만" not in _SYSTEM_PROMPT
    # D-056 ①: 라우터 v9 의 `general` 목적지를 `answer_generally` 로 거울 — 더해서, 대신은 아니다.
    assert "answer_generally" in _SYSTEM_PROMPT
    assert "**더해** 부르고 대신하지 않으며" in _SYSTEM_PROMPT
    assert (
        "반려견과 무관한 요청" in _SYSTEM_PROMPT
        and "어느\n  도구도 부르지 않습니다" in _SYSTEM_PROMPT
    )
    from daengs_backend.orchestration.agent.tools import CapabilityToolbox

    box = CapabilityToolbox()
    general_tool = next(tool for tool in box.as_tools() if tool.name == "answer_generally")
    assert general_tool.args == {}  # 인자 없음 — payload 는 planner 가 만든다 (D-051)


async def test_agent_toolbox_passes_general_through_like_any_execute_name() -> None:
    pytest.importorskip("langchain")
    from daengs_backend.orchestration.agent.tools import CapabilityToolbox

    box = CapabilityToolbox()
    tools = {tool.name: tool for tool in box.as_tools()}
    await tools["check_walk_conditions"].ainvoke({})
    await tools["answer_generally"].ainvoke({})
    assert box.decision() == SemanticRoutingDecision(execute=["walk", "general"])


def test_life_payload_and_general_payload_share_the_dog_type() -> None:
    dog = DogContext(breed="푸들", age_months=30)
    assert LifePayload(question="q", dog=dog).dog == GeneralPayload(question="q", dog=dog).dog


def test_answer_schema_requires_text_so_the_model_cannot_omit_it() -> None:
    """라이브 확인에서 모델이 `{"kind": "answer", "reason": null}` 로 text 를 통째로 빼고 답했다.

    스키마에서 text 가 선택 필드면 제약 디코딩이 그것을 허용한다. 필수로 두어야 답 경로가
    `general_invalid_output` 으로 새지 않는다. 거절은 text 를 "" 으로 낸다.
    """
    from daengs_backend.orchestration.adapters.general import GeneralAnswer, validate_general_answer

    assert "text" in GeneralAnswer.model_json_schema()["required"]
    assert validate_general_answer({"kind": "answer", "reason": None}) is None
    assert (
        validate_general_answer({"kind": "answer", "text": "짧은 답", "reason": None}) is not None
    )
    assert (
        validate_general_answer({"kind": "refuse", "text": "", "reason": "emergency"}) is not None
    )
