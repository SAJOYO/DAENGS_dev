"""일반 답변 폴백 (#279) — 결정론 부분만 잰다. 프로바이더 호출 0.

폴백은 라우터의 목적지가 아니라 **planner 의 규칙**이다: 라우터가 전문 능력을 하나도 안
골랐고 핸드오프도 없으면, 플래그(`DAENGS_GENERAL_FALLBACK`)가 켜져 있을 때만 `general`
요청 하나를 조립한다. 이 파일이 고정하는 것:

- 플래그가 꺼져 있으면(기본) 빈 결정은 그대로 FAILED 다 — 문구만 스코프드 리다이렉트로
  바뀌었다 (#278).
- 켜져 있으면 빈 결정 → `general` 하나, payload 는 Life 와 같은 규칙(원문 + 신뢰된 dog).
- 전문 능력이나 핸드오프가 하나라도 있으면 `general` 이 **절대** 안 붙는다.
- 좌표 게이트(CLARIFY)가 폴백보다 먼저다. 명시 신호는 영향이 없고 `general` 은 신호가 아니다.
- 라우터 스키마와 프롬프트는 `general` 을 모른다 — 모델이 고르게 하면 근거 있는 답을 근거
  없는 답으로 바꾸는 오선택이 생기고, 그 방향의 실수가 가장 나쁘다.
- 어댑터는 answer/refuse/프로바이더 실패를 OK/REFUSED/ERROR 로 옮기고, 거절 문구는 모델이
  아니라 코드가 쓴다.
"""

from __future__ import annotations

import json
import re
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
from daengs_backend.orchestration.redirects import (
    DISTANCE_FROM_RECORDED_WALKS_ONLY,
    NO_CAPABILITY_MESSAGE,
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
from daengs_evals.router_benchmark.evaluate import ALLOWED_EXECUTE, evaluate_benchmark
from daengs_evals.router_benchmark.schemas import load_gold_v3_cases

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
    assert set(request.payload.model_dump()) == {
        "question",
        "dog",
        "care_log",
        "vet_spend",
        "walk_activity",
        "conversation",
    }
    assert request.payload.care_log is None
    assert request.payload.vet_spend is None
    # walk_activity 도 같은 규칙 (D-073) — 이 호출은 산책 기록을 넘기지 않는다.
    assert request.payload.walk_activity is None
    # Resolver 를 거치지 않은 호출(`resolved` 미지정)이라 conversation 도 비어 있다 (#416 Task 5).
    assert request.payload.conversation is None


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
    """D-057 ①: 라우터가 전문 능력에 **더해** 고른 `general` 은 살아남고 맨 뒤에 선다."""
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
    """D-057 ①: v9 부터 모델이 `general` 을 고를 수 있다 — 전문 능력에 더해서, 대신해서는 아니다."""
    assert get_args(ExecuteName)[-1] == "general"
    assert (
        validate_semantic_decision(json.dumps({"execute": ["general"], "handoffs": []})) is not None
    )
    assert validate_semantic_decision(json.dumps({"execute": ["walk", "general"], "handoffs": []}))
    prompt = build_semantic_router_prompt(query="x", context={})
    assert "execute.general:" in prompt
    flat = " ".join(prompt.split())
    assert "IN ADDITION to any specialized destination" in flat
    assert "never replaces Training, Life, Walk, or Place" in flat
    assert "not about dogs at all" in flat and "selects NOTHING" in flat
    # The narrowing after the first v9 draft over-selected (32/80, every training case):
    # a SEPARATE care question is required, full coverage by a specialized destination
    # means no General, context mentions are not triggers, doubt → Training alone.
    assert "ONLY when the utterance contains a SEPARATE general-care" in flat
    assert (
        "fully covered by Training (changing behavior or teaching a skill), Life, Walk, or Place "
        "gets NO General" in flat
    )
    assert "a symptom mentioned as context does not make a request General" in flat
    assert (
        "When in doubt between Training and General for a behavior question, choose Training alone"
        in flat
    )
    assert "behavior-as-wellbeing" not in prompt


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


async def test_flag_off_empty_decision_is_failed_with_the_scoped_redirect() -> None:
    orchestrator, fakes = service({"execute": [], "handoffs": []}, ok(CapabilityName.GENERAL, "x"))
    response = await orchestrator.run(query=QUERY, principal=PRINCIPAL, context=dict(SEOUL))
    assert response.status == AssistantStatus.FAILED
    assert response.message == NO_CAPABILITY_MESSAGE
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
    """`care_log` 만 기본 어댑터가 없다 — **요청마다 만들어 넣어야 하는 유일한 능력**이다.

    `app_user_id` 를 들고 있고 그것이 "누구 이름으로 기록되는가" 라서다 (D-074).
    전역 어댑터로 두고 payload 에 사용자를 실으면, 그 값이 어디서 왔는지를 payload 검증이
    보장하지 못한다. 안 넣은 요청은 애초에 제안을 못 받으므로(`context["care_log_writable"]`)
    승낙받을 것도 없다 — `graph.py` 의 그 `if` 주석이 이 쌍을 설명한다.
    """
    registered = set(OrchestrationEngine()._adapters)
    assert registered == set(CapabilityName) - {CapabilityName.CARE_LOG}
    assert CapabilityName.CARE_LOG in set(OrchestrationEngine(care_log_adapter=_FakeCareLog())._adapters)


class _FakeCareLog:
    capability = CapabilityName.CARE_LOG

    async def run(self, request, *, request_id):  # pragma: no cover - 등록만 보는 대역
        raise AssertionError("등록만 확인한다")


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


def test_unmeasured_belongs_to_an_answer_only() -> None:
    """거절·되묻기는 이 마커를 못 든다 — 계약이 막는다.

    `axes` 가 되묻기에만 붙는 것과 같은 규칙이다. 거절에 붙으면 리다이렉트 문구 뒤에
    고지가 또 붙어 같은 상황이 두 문장으로 나간다. `ask` 조합도 함께 확인한다 —
    `shape_matches_kind` 의 `kind == "ask"` 이른 `return self` 보다 이 검사가 앞에
    있어야 되묻기에 붙은 마커를 잡아낸다.
    """
    assert (
        validate_general_answer(
            {"kind": "answer", "text": "기록이 없어요.", "reason": None, "unmeasured": True}
        )
        is not None
    )
    assert (
        validate_general_answer(
            {"kind": "refuse", "text": "", "reason": "diagnosis", "unmeasured": True}
        )
        is None
    )
    assert (
        validate_general_answer(
            {
                "kind": "ask",
                "text": "오늘은 기록이 없어요.",
                "question": "오늘 컨디션이 어때 보이나요?",
                "reason": None,
                "unmeasured": True,
            }
        )
        is None
    )


async def test_the_adapter_appends_the_fixed_sentence_when_the_marker_is_set() -> None:
    """문장은 코드가 붙인다 — 모델 산문이 아니다 (#278)."""
    result, _ = await run_adapter(
        json.dumps(
            {
                "kind": "answer",
                "text": "버스 구간은 걷지 않으셨어요.",
                "reason": None,
                "unmeasured": True,
            }
        )
    )
    assert result.status == CapabilityStatus.OK
    assert result.data["answer"].endswith(DISTANCE_FROM_RECORDED_WALKS_ONLY)
    # 본문은 손대지 않는다 — 무손실
    assert result.data["answer"].startswith("버스 구간은 걷지 않으셨어요.")


async def test_no_marker_means_no_sentence() -> None:
    """급여량을 물어본 사람에게 산책 고지가 따라붙으면 안 된다 — 마커를 고른 이유 그 자체."""
    result, _ = await run_adapter(
        json.dumps({"kind": "answer", "text": "하루 두 번이 보통이에요.", "reason": None})
    )
    assert result.status == CapabilityStatus.OK
    assert DISTANCE_FROM_RECORDED_WALKS_ONLY not in result.data["answer"]


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
    # "lat" as a whole word — the English v3 prompt legitimately contains "regulations"
    assert "37.5" not in prompt
    assert not re.search(r"\blat\b|\blon\b", prompt.split("GENERAL_ANSWER_JSON_SCHEMA")[0])
    # 안전 경계가 프롬프트에 실제로 있다 — 진단 · 약/용량 · 응급 · 제도/수치 · 도메인 밖.
    for word in ("diagnosis", "medication", "emergency", "institutional", "off_topic"):
        assert word in prompt
    assert "veterinary hospital" in prompt and "institutional-information capability" in prompt
    assert build_general_prompt(GeneralPayload(question=QUERY)).count("DOG_CONTEXT: {}") == 1


def test_general_prompt_carries_the_care_facts_but_never_a_drug_name() -> None:
    """#331: the dog block widens to feeding style, conditions and *whether* it is on
    medication. The safety prompt text itself stays at v3 — that text was approved after a
    paired comparison (D-057 ③) and this card only changes the JSON that flows into it."""
    payload = GeneralPayload(
        question=QUERY,
        dog=DogContext(
            breed="푸들", feeding_style="scheduled",
            health_conditions="신부전 초기", on_medication=True,
        ),
    )
    prompt = build_general_prompt(payload)
    assert (
        'DOG_CONTEXT: {"breed": "푸들", "feeding_style": "scheduled",'
        ' "health_conditions": "신부전 초기", "on_medication": true}'
    ) in prompt
    assert GENERAL_PROMPT_VERSION == "general-answer-ko-v10"
    # the contract has no field that could carry a drug name into the prompt
    assert "medications" not in DogContext.model_fields
    assert "feeding_times" not in DogContext.model_fields


def test_safety_prompt_v2_answers_husbandry_norms_and_narrows_the_refusals() -> None:
    """D-057 ③ⓐ: v1 refused feeding-amount / water-intake norms as institutional or
    diagnosis (#277: 7 of 15 general_care). v2 names those norms answerable with a hedge,
    makes institutional document-backed facts only, and diagnosis explicit requests only."""
    assert GENERAL_PROMPT_VERSION == "general-answer-ko-v10"
    prompt = build_general_prompt(GeneralPayload(question=QUERY))
    # v3: the instructions are English like the router policy; the OUTPUT stays Korean
    assert "Write in Korean" in prompt
    assert "당신은" not in prompt
    # husbandry norms are answerable, with the individual-variation hedge and the authority
    assert "Ordinary husbandry norms ARE answerable" in prompt
    for topic in (
        "feeding frequency",
        "water intake",
        "bathing",
        "brushing",
        "nail-trimming",
        "walking gear",
        "socialization timing",
        "sleep duration",
    ):
        assert topic in prompt, topic
    assert "individual variation is large" in prompt
    assert "feeding table on the food package or the veterinarian is the authority" in prompt
    assert "These ordinary norms are NOT institutional" in prompt
    # "is this normal" answers with the range and a vet hedge — it is not a diagnosis
    assert "is this normal" in prompt and "Do not refuse it" in prompt
    assert '"Is this okay / is this normal" is NOT diagnosis' in prompt
    # institutional = document-backed facts only; diagnosis = explicit requests only
    assert "facts that require a source document" in prompt
    assert "Ordinary husbandry numbers do NOT belong here" in prompt
    assert "explicitly asks for a disease name" in prompt and "interpret test results" in prompt
    # emergency · off_topic unchanged; medication narrowed by D-071 (covered separately below)
    assert 'Say nothing beyond "go to a veterinary hospital right now"' in prompt
    assert "off_topic: the question is not about dogs." in prompt


def test_medication_boundary_answers_duration_or_interval_without_requiring_confirmation() -> None:
    """D-071: 약의 기간·투여 간격은 답한다 — **이미 복용 중임을 확인할 것을 요구하지 않는다.**

    #446 의 실물: "심장사상충 예방약 얼마나 오래 해야 해?" 가 medication 거절이었다. 초판은
    "이미 복용 중"이 확인돼야 답하도록 게이트를 걸었는데, `DogContext.on_medication` 은
    이름 없는 `True`/`None` 뿐이라 모델이 그 확인을 할 방법이 없었다 — 그 결과 #446 의
    동기 사례 자체가 다시 거절로 떨어졌다(리뷰가 잡음). 지금은 확인 여부가 아니라 **질문의
    형태**(기간·주기 대 시작 여부)로 가른다 — "confirmed already on" 이 프롬프트에 남아
    있지 않은지도 같이 잰다."""
    prompt = build_general_prompt(GeneralPayload(question=QUERY))
    assert "duration or dosing interval of a medication is answerable" in prompt
    assert "confirmed already on" not in prompt
    # 기간·투여 간격 규칙이 husbandry norm 규칙 다음, refuse 목록보다 앞(answering 규칙)에 있다
    husbandry_idx = prompt.index("Ordinary husbandry norms ARE answerable")
    duration_idx = prompt.index("duration or dosing interval of a medication")
    refuse_idx = prompt.index('Refuse (kind="refuse") only in these cases')
    assert husbandry_idx < duration_idx < refuse_idx


def test_medication_boundary_still_refuses_dosage_questions() -> None:
    """용량은 A 범위 밖 — 계속 거절이어야 한다. 단어 하나가 아니라 절 전체를 고정한다 —
    `medication: dosages.` 로 규칙이 쪼그라들어도 통과하는 단어 단위 단언은 경계를 안 잰다."""
    prompt = build_general_prompt(GeneralPayload(question=QUERY))
    medication_rule = prompt[prompt.index("- medication:") : prompt.index("- emergency:")]
    assert (
        "which drug or supplement to give, whether to start one, dosages, "
        "how to give it (timing, with food, splitting a pill), or side effects"
    ) in medication_rule


def test_medication_boundary_still_refuses_drug_name_recommendations() -> None:
    """무슨 약을 먹일지 이름 추천은 A 범위 밖 — 계속 거절이어야 한다."""
    prompt = build_general_prompt(GeneralPayload(question=QUERY))
    medication_rule = prompt[prompt.index("- medication:") : prompt.index("- emergency:")]
    assert "which drug or supplement to give" in medication_rule


def test_medication_boundary_still_refuses_whether_to_start_a_drug() -> None:
    """새로 시작할지 여부는 A 범위 밖 — 계속 거절이어야 한다. 이 축을 여는 것이 바로 이
    카드가 열지 않기로 한 것이라, 절 전체(질문 형태로 가르는 문장)를 고정한다 — 단어 하나
    (`"whether to start one" in ...`)만 보면 (지금은 지운) tiebreak 문장에도 같은 단어가
    있어서 그 문장을 지워도 우연히 계속 통과했다."""
    prompt = build_general_prompt(GeneralPayload(question=QUERY))
    medication_rule = prompt[prompt.index("- medication:") : prompt.index("- emergency:")]
    assert (
        "a question about whether to begin one is whether to start one, and refused"
    ) in medication_rule


def test_medication_boundary_still_refuses_administration_instructions() -> None:
    """복용 방법(몇 시에 · 밥과 함께 · 쪼개서)은 A 범위 밖 — "duration or schedule" 이었을 때
    새어 나갔을 축이다. dosing interval 로 좁힌 뒤에도 여기서 막힌다."""
    prompt = build_general_prompt(GeneralPayload(question=QUERY))
    medication_rule = prompt[prompt.index("- medication:") : prompt.index("- emergency:")]
    assert "how to give it (timing, with food, splitting a pill)" in medication_rule


def test_medication_boundary_still_refuses_side_effects_information_not_only_judgment() -> None:
    """부작용은 판단("이거 부작용이야?")뿐 아니라 정보("흔한 부작용이 뭐야?")도 닫는다 —
    D-071 이 기각한 B(약 일반 정보, 흔한 부작용 포함)로 새는 자리라 별도로 고정한다."""
    prompt = build_general_prompt(GeneralPayload(question=QUERY))
    medication_rule = prompt[prompt.index("- medication:") : prompt.index("- emergency:")]
    assert (
        "side effects — what they are in general, or whether something the owner describes is one"
    ) in medication_rule
    # 증상 답변 쪽의 "일반 기전은 설명 가능" 규칙이 약에도 적용된다고 모델이 읽지 않게,
    # 그 규칙 자신이 범위를 증상으로 좁혀 둔다.
    mechanism_rule = prompt[
        prompt.index("A general mechanism may be explained") : prompt.index(
            "A symptom the owner mentions"
        )
    ]
    assert "not drugs" in mechanism_rule and "refused as medication" in mechanism_rule


def test_medication_boundary_is_this_normal_rule_excludes_medication() -> None:
    """administration 이 거절 목록에 다시 들어오며 husbandry 의 "is this okay / is this
    normal" 규칙과 충돌이 넓어졌다 — "두 배로 줘도 돼?" 는 그 규칙 형태 그대로의 intake
    amount 질문이고, "밥이랑 같이 먹여도 돼?" · "쪼개서 줘도 돼?" 는 같은 형태의 behavior
    질문이다. 그 규칙 자신이 범위를 husbandry 로 좁혀, 약 질문은 여기서 안 걸리고
    medication 으로 떨어지는지를 잰다 — 원인-단정 규칙(:187)에 이미 적용한 것과 같은
    수선이다."""
    prompt = build_general_prompt(GeneralPayload(question=QUERY))
    is_normal_rule = prompt[
        prompt.index('A question of the form "is this okay') : prompt.index(
            "Say you do not know when unsure"
        )
    ]
    assert "not medication" in is_normal_rule
    assert "refused as medication" in is_normal_rule


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
