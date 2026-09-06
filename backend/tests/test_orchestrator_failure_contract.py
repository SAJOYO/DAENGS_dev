"""실패 계약 — 두 구현에 같은 시나리오를 먹여 같은 답이 나오는지 결정론으로 본다 (#272).

비교 벤치마크의 가짜 어댑터는 항상 OK 라 오류·타임아웃·혼합이 거기서는 안 보인다. 여기서
그것을 따로 잰다. **이 파일은 80케이스 점수와 무관하다** — 골드도 러너도 건드리지 않는다.

시나리오마다 확인하는 것: 어댑터 실행이 시작됐나 · 실행 순서 · 성공 결과 보존 · 오류/타임아웃
보존 · 응답 상태 · 핸드오프 보존 · 조용한 의도 손실 없음 · 두 구현의 동치.

**계약이 모호한 자리는 규칙을 새로 만들지 않고 적어 둔다:**

- ⓐ `assemble_route_plan` 이 만드는 요청은 `timeout_ms=None` 이라, 두 의미 경로 모두에서
  엔진의 `wait_for` 데드라인이 걸리지 않는다. 오케스트레이션 타임아웃은 어댑터 자신의 프로바이더
  타임아웃(TIMEOUT 결과)으로만 나타난다. 그래서 "타임아웃" 시나리오는 어댑터가 TIMEOUT 을
  돌려주는 것으로 잰다.
- ⓑ 실행이 전부 실패하고 핸드오프가 같이 있으면 진리표(`aggregate.py`)는 FAILED 를 내면서
  핸드오프는 응답에 남긴다. HANDOFF 가 이겨야 하는지는 계약이 말하지 않는다. 두 구현이 같은
  코드를 지나므로 같다 — 그것만 확인한다.
- ⓒ 계획 동결 전 모델 실패에서 에이전트는 이미 고른 선택을 버린다 (v1 은 실행된 결과를
  살렸다). 실행이 선택 뒤이므로 살릴 결과가 없고, 공유 라우터 실패 계약(O-14)을 따른다.
- ⓓ 일반 답변 폴백(#279)은 두 구현이 **같은 planner 규칙을 같은 플래그로** 지난다. 라우터의
  빈 결정과 에이전트의 "툴 없이 마침"은 플래그가 꺼져 있으면 둘 다 FAILED, 켜져 있으면 둘 다
  `general` 하나를 같은 payload 로 돌린다. 전문 선택에는 어느 쪽도 `general` 을 안 붙인다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("langchain")

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from daengs_backend.config import settings
from daengs_backend.orchestration.agent.service import AgentOrchestrationService
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityName,
    CapabilityResult,
    CapabilityStatus,
    ErrorDetail,
    GeneralPayload,
    OutcomeDetail,
    PrincipalContext,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.semantic import GeminiSemanticRouter
from daengs_backend.orchestration.service import (
    _ROUTER_FAILURE_MESSAGE,
    AssistantOrchestrationService,
)

PRINCIPAL = PrincipalContext(subject="test-user", kind="APP_USER")
SEOUL = {"location": {"lat": 37.5, "lon": 127.0}}
QUERY = "질문"

TOOL_BY_CAPABILITY = {
    "training": "ask_training",
    "life": "ask_life",
    "walk": "check_walk_conditions",
    "place": "search_places",
    "general": "answer_generally",
}
TOOL_BY_HANDOFF = {"skin": "hand_off_to_skin", "gait": "hand_off_to_gait"}


# ── 어댑터 대본 ──────────────────────────────────────────────────────────


def ok(capability: CapabilityName) -> CapabilityResult:
    return CapabilityResult(
        capability=capability,
        status=CapabilityStatus.OK,
        data={"answer": f"{capability.value} 답"},
        elapsed_ms=1,
    )


def timeout(capability: CapabilityName) -> CapabilityResult:
    """어댑터 자신의 데드라인 (ⓐ). 실제 어댑터가 프로바이더 타임아웃에 돌려주는 모양이다."""
    return CapabilityResult(
        capability=capability,
        status=CapabilityStatus.TIMEOUT,
        error=ErrorDetail(kind="provider_timeout", detail="응답 시간이 초과됐습니다."),
        elapsed_ms=30_000,
    )


class ScriptedAdapter:
    def __init__(self, capability: CapabilityName, outcome, log: list[str]) -> None:
        self.capability = capability
        self._outcome = outcome
        self._log = log
        self.payloads: list[object] = []

    async def run(self, request, *, request_id: str) -> CapabilityResult:
        self._log.append(self.capability.value)
        self.payloads.append(request.payload)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def adapters(
    outcomes: dict[str, object],
) -> tuple[dict[CapabilityName, ScriptedAdapter], list[str]]:
    log: list[str] = []
    fakes = {
        CapabilityName(name): ScriptedAdapter(CapabilityName(name), outcome, log)
        for name, outcome in outcomes.items()
    }
    return fakes, log


# ── 선택 대본 ────────────────────────────────────────────────────────────


class ScriptedChatModel(BaseChatModel):
    """대본은 AIMessage 또는 예외다 — 예외면 그 턴에서 프로바이더가 죽은 것으로 친다."""

    script: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        message = self.script.pop(0) if self.script else AIMessage(content="마쳤습니다.")
        if isinstance(message, Exception):
            raise message
        return ChatResult(generations=[ChatGeneration(message=message)])


def tool_calls(execute: list[str], handoffs: list[str]) -> AIMessage:
    names = [TOOL_BY_CAPABILITY[n] for n in execute] + [TOOL_BY_HANDOFF[h] for h in handoffs]
    return AIMessage(
        content="",
        tool_calls=[{"name": n, "args": {}, "id": f"c{i}"} for i, n in enumerate(names)],
    )


class ScriptedTransport:
    def __init__(self, *outputs: object) -> None:
        self.outputs = list(outputs)

    async def __call__(self, prompt: str) -> object:
        result = self.outputs.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def langgraph_service(transport, fakes) -> AssistantOrchestrationService:
    return AssistantOrchestrationService(
        engine=OrchestrationEngine(fakes), semantic_router=GeminiSemanticRouter(generate=transport)
    )


def agent_service(model, fakes) -> AgentOrchestrationService:
    return AgentOrchestrationService(model=model, engine=OrchestrationEngine(fakes))


async def run_both(
    execute: list[str], handoffs: list[str], outcomes: dict[str, object], *, context=None
) -> tuple[AssistantResponse, list[str], AssistantResponse, list[str]]:
    """같은 선택(라우터 JSON / 에이전트 툴 호출)과 같은 어댑터 대본을 두 구현에 먹인다."""
    lg_fakes, lg_log = adapters(outcomes)
    ag_fakes, ag_log = adapters(outcomes)
    decision = json.dumps({"execute": execute, "handoffs": handoffs})
    ctx = dict(SEOUL if context is None else context)
    lg = await langgraph_service(ScriptedTransport(decision), lg_fakes).run(
        query=QUERY, principal=PRINCIPAL, context=dict(ctx)
    )
    ag = await agent_service(
        ScriptedChatModel(script=[tool_calls(execute, handoffs)]), ag_fakes
    ).run(query=QUERY, principal=PRINCIPAL, context=dict(ctx))
    return lg, lg_log, ag, ag_log


def assert_equivalent(lg: AssistantResponse, ag: AssistantResponse) -> None:
    """두 구현의 동치. `request_id` 만 다르다."""
    assert lg.status == ag.status
    assert lg.message == ag.message
    assert [(r.capability, r.status, r.error) for r in lg.results] == [
        (r.capability, r.status, r.error) for r in ag.results
    ]
    assert lg.handoffs == ag.handoffs
    assert lg.clarify == ag.clarify


def assert_no_silent_intent_loss(
    response: AssistantResponse, execute: list[str], handoffs: list[str]
) -> None:
    """고른 의도가 전부 응답에 남았다 — 결과 한 줄(성공이든 오류든) 또는 핸드오프로."""
    assert [r.capability.value for r in response.results] == execute
    assert [h.target for h in response.handoffs] == handoffs


# ── 시나리오 ─────────────────────────────────────────────────────────────


async def test_first_succeeds_second_errors() -> None:
    lg, lg_log, ag, ag_log = await run_both(
        ["training", "life"],
        [],
        {"training": ok(CapabilityName.TRAINING), "life": RuntimeError("boom")},
    )
    assert lg_log == ag_log == ["training", "life"]  # 첫 실패 뒤에도 순서대로 끝까지 돈다
    assert lg.status == AssistantStatus.PARTIAL
    assert [r.status for r in lg.results] == [CapabilityStatus.OK, CapabilityStatus.ERROR]
    assert lg.results[1].error is not None and "boom" not in lg.results[1].error.detail
    assert "training 답" in lg.message
    assert_no_silent_intent_loss(lg, ["training", "life"], [])
    assert_equivalent(lg, ag)


async def test_first_errors_second_succeeds() -> None:
    lg, lg_log, ag, ag_log = await run_both(
        ["training", "life"],
        [],
        {"training": RuntimeError("boom"), "life": ok(CapabilityName.LIFE)},
    )
    assert lg_log == ag_log == ["training", "life"]
    assert lg.status == AssistantStatus.PARTIAL
    assert [r.status for r in lg.results] == [CapabilityStatus.ERROR, CapabilityStatus.OK]
    assert "life 답" in lg.message
    assert_no_silent_intent_loss(lg, ["training", "life"], [])
    assert_equivalent(lg, ag)


async def test_one_succeeds_and_another_times_out() -> None:
    """ⓐ — 타임아웃은 어댑터의 결과로 온다. 성공 결과는 남고 타임아웃도 한 줄로 남는다."""
    lg, lg_log, ag, ag_log = await run_both(
        ["training", "walk"],
        [],
        {"training": ok(CapabilityName.TRAINING), "walk": timeout(CapabilityName.WALK)},
    )
    assert lg_log == ag_log == ["training", "walk"]
    assert lg.status == AssistantStatus.PARTIAL
    assert [r.status for r in lg.results] == [CapabilityStatus.OK, CapabilityStatus.TIMEOUT]
    assert lg.results[1].error is not None and lg.results[1].error.kind == "provider_timeout"
    assert_no_silent_intent_loss(lg, ["training", "walk"], [])
    assert_equivalent(lg, ag)


async def test_all_executable_capabilities_fail() -> None:
    lg, lg_log, ag, ag_log = await run_both(
        ["training", "life"],
        [],
        {"training": RuntimeError("a"), "life": timeout(CapabilityName.LIFE)},
    )
    assert lg_log == ag_log == ["training", "life"]  # 첫 실패가 둘째를 건너뛰게 하지 않는다
    assert lg.status == AssistantStatus.FAILED
    assert [r.status for r in lg.results] == [CapabilityStatus.ERROR, CapabilityStatus.TIMEOUT]
    assert all(r.error is not None for r in lg.results)
    assert_no_silent_intent_loss(lg, ["training", "life"], [])
    assert_equivalent(lg, ag)


async def test_mixed_execution_and_handoff_with_an_execution_failure() -> None:
    """ⓑ — 실행이 전부 실패해도 핸드오프는 남고, 상태는 진리표대로 FAILED 다. 둘이 같다."""
    lg, lg_log, ag, ag_log = await run_both(
        ["training"], ["gait"], {"training": RuntimeError("boom")}
    )
    assert lg_log == ag_log == ["training"]
    assert lg.status == AssistantStatus.FAILED
    assert [h.target for h in lg.handoffs] == ["gait"]
    assert "보행 영상" in lg.message  # 핸드오프 안내 문구가 붙는다
    assert_no_silent_intent_loss(lg, ["training"], ["gait"])
    assert_equivalent(lg, ag)


async def test_mixed_execution_and_handoff_with_a_partial_failure() -> None:
    lg, lg_log, ag, ag_log = await run_both(
        ["training", "life"],
        ["skin"],
        {"training": ok(CapabilityName.TRAINING), "life": RuntimeError("boom")},
    )
    assert lg_log == ag_log == ["training", "life"]
    assert lg.status == AssistantStatus.PARTIAL
    assert [h.target for h in lg.handoffs] == ["skin"]
    assert_no_silent_intent_loss(lg, ["training", "life"], ["skin"])
    assert_equivalent(lg, ag)


async def test_selector_failure_before_plan_freeze_runs_nothing() -> None:
    """ⓒ — 라우터 프로바이더 실패와 에이전트 모델 실패(첫 턴·툴을 고른 뒤 마무리 턴)는 전부
    같은 O-14 응답이다: FAILED, 공유 문구, 어댑터 0회, CLARIFY 아님."""
    fakes_lg, log_lg = adapters({"training": ok(CapabilityName.TRAINING)})
    lg = await langgraph_service(ScriptedTransport(RuntimeError("provider down")), fakes_lg).run(
        query=QUERY, principal=PRINCIPAL, context=dict(SEOUL)
    )

    fakes_first, log_first = adapters({"training": ok(CapabilityName.TRAINING)})
    first_turn = await agent_service(
        ScriptedChatModel(script=[RuntimeError("provider down")]),
        fakes_first,  # type: ignore[list-item]
    ).run(query=QUERY, principal=PRINCIPAL, context=dict(SEOUL))

    fakes_mid, log_mid = adapters({"training": ok(CapabilityName.TRAINING)})
    mid_loop = await agent_service(
        ScriptedChatModel(script=[tool_calls(["training"], []), RuntimeError("provider down")]),  # type: ignore[list-item]
        fakes_mid,
    ).run(query=QUERY, principal=PRINCIPAL, context=dict(SEOUL))

    for response in (lg, first_turn, mid_loop):
        assert response.status == AssistantStatus.FAILED
        assert response.message == _ROUTER_FAILURE_MESSAGE
        assert response.results == [] and response.handoffs == [] and response.clarify is None
    assert log_lg == log_first == log_mid == []
    assert_equivalent(lg, first_turn)
    assert_equivalent(lg, mid_loop)


async def test_empty_selection_is_the_same_failed_answer_in_both() -> None:
    """라우터의 빈 결정과 에이전트의 '툴 없이 마침'은 같은 길로 FAILED 다 — 어댑터 0회.

    플래그가 **꺼진** 기본값에서의 계약이다 (ⓓ). 켜졌을 때는 아래 `run_both_empty` 계열이 잰다.
    """
    assert settings.general_fallback is False
    fakes_lg, log_lg = adapters({"training": ok(CapabilityName.TRAINING)})
    lg = await langgraph_service(
        ScriptedTransport(json.dumps({"execute": [], "handoffs": []})), fakes_lg
    ).run(query=QUERY, principal=PRINCIPAL, context=dict(SEOUL))
    fakes_ag, log_ag = adapters({"training": ok(CapabilityName.TRAINING)})
    ag = await agent_service(
        ScriptedChatModel(script=[AIMessage(content="답할 수 없어요.")]), fakes_ag
    ).run(query=QUERY, principal=PRINCIPAL, context=dict(SEOUL))
    assert lg.status == ag.status == AssistantStatus.FAILED
    assert "답할 수 없어요" not in ag.message
    assert log_lg == log_ag == []
    assert_equivalent(lg, ag)


# ── ⓓ 일반 답변 폴백 (#279) — 켜졌을 때 두 구현이 같은 길을 지난다 ──────


def refused_general() -> CapabilityResult:
    return CapabilityResult(
        capability=CapabilityName.GENERAL,
        status=CapabilityStatus.REFUSED,
        refusal=OutcomeDetail(code="medication", message="수의사에게 확인해 주세요."),
        elapsed_ms=1,
    )


async def run_both_empty(
    outcomes: dict[str, object], *, context=None
) -> tuple[AssistantResponse, dict, AssistantResponse, dict]:
    """빈 라우터 결정 / 툴 없이 마친 에이전트 — 같은 어댑터 대본을 두 구현에 먹인다."""
    lg_fakes, _ = adapters(outcomes)
    ag_fakes, _ = adapters(outcomes)
    ctx = dict(SEOUL if context is None else context)
    lg = await langgraph_service(
        ScriptedTransport(json.dumps({"execute": [], "handoffs": []})), lg_fakes
    ).run(query=QUERY, principal=PRINCIPAL, context=dict(ctx))
    ag = await agent_service(
        ScriptedChatModel(script=[AIMessage(content="맞는 도구가 없어 마칩니다.")]), ag_fakes
    ).run(query=QUERY, principal=PRINCIPAL, context=dict(ctx))
    return lg, lg_fakes, ag, ag_fakes


@pytest.fixture
def fallback_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "general_fallback", True)


async def test_empty_selection_with_fallback_on_is_the_same_general_answer_in_both(
    fallback_on: None,
) -> None:
    lg, lg_fakes, ag, ag_fakes = await run_both_empty(
        {"training": ok(CapabilityName.TRAINING), "general": ok(CapabilityName.GENERAL)},
        context={**SEOUL, "dog": {"breed": "푸들", "age_months": 30}},
    )
    for response, fakes in ((lg, lg_fakes), (ag, ag_fakes)):
        assert response.status == AssistantStatus.ANSWERED
        assert response.message == "general 답"
        assert fakes[CapabilityName.TRAINING].payloads == []
        assert fakes[CapabilityName.GENERAL].payloads == [
            GeneralPayload(question=QUERY, dog={"breed": "푸들", "age_months": 30})
        ]
    assert "마칩니다" not in ag.message  # 에이전트의 마무리 문장은 여전히 버린다
    assert_no_silent_intent_loss(lg, ["general"], [])
    assert_equivalent(lg, ag)


async def test_general_refusal_with_fallback_on_is_refused_in_both(fallback_on: None) -> None:
    lg, _, ag, _ = await run_both_empty({"general": refused_general()})
    assert lg.status == ag.status == AssistantStatus.REFUSED
    assert lg.message == "수의사에게 확인해 주세요."
    assert lg.results[0].refusal is not None and lg.results[0].refusal.code == "medication"
    assert_equivalent(lg, ag)


async def test_general_provider_failure_with_fallback_on_is_contained_in_both(
    fallback_on: None,
) -> None:
    """폴백이 죽어도 사용자에게는 능력 하나의 실패로 보인다 — 라우터 실패 문구가 아니다."""
    lg, _, ag, _ = await run_both_empty({"general": RuntimeError("boom")})
    assert lg.status == ag.status == AssistantStatus.FAILED
    assert lg.message != _ROUTER_FAILURE_MESSAGE
    assert [r.status for r in lg.results] == [CapabilityStatus.ERROR]
    assert "boom" not in lg.message
    assert_no_silent_intent_loss(lg, ["general"], [])
    assert_equivalent(lg, ag)


async def test_specialized_selection_with_fallback_on_never_runs_general_in_both(
    fallback_on: None,
) -> None:
    lg, lg_log, ag, ag_log = await run_both(
        ["training"],
        ["gait"],
        {"training": ok(CapabilityName.TRAINING), "general": ok(CapabilityName.GENERAL)},
    )
    assert lg_log == ag_log == ["training"]
    assert_no_silent_intent_loss(lg, ["training"], ["gait"])
    assert_equivalent(lg, ag)


async def test_mixed_general_and_walk_selection_runs_both_in_planner_order_in_both(
    fallback_on: None,
) -> None:
    """D-056 ①: 라우터가 `general` 을 산책에 **더해** 골랐다 / 에이전트가 두 툴을 불렀다 — 둘 다
    산책 → 일반 순으로 돌고 돌봄 의도가 사라지지 않는다."""
    lg, lg_log, ag, ag_log = await run_both(
        ["general", "walk"],
        [],
        {"walk": ok(CapabilityName.WALK), "general": ok(CapabilityName.GENERAL)},
    )
    assert lg_log == ag_log == ["walk", "general"]
    assert lg.status == ag.status == AssistantStatus.ANSWERED
    assert "[산책]" in lg.message and "[일반]" in lg.message
    assert_no_silent_intent_loss(lg, ["walk", "general"], [])
    assert_equivalent(lg, ag)


async def test_mixed_general_and_walk_selection_drops_general_in_both_when_flag_is_off() -> None:
    """플래그가 꺼져 있으면 두 구현 다 예전 계획이다 — `general` 은 planner 가 떼어 낸다."""
    assert settings.general_fallback is False
    lg, lg_log, ag, ag_log = await run_both(
        ["general", "walk"],
        [],
        {"walk": ok(CapabilityName.WALK), "general": ok(CapabilityName.GENERAL)},
    )
    assert lg_log == ag_log == ["walk"]
    assert_equivalent(lg, ag)


async def test_clarify_gate_with_fallback_on_still_runs_nothing_in_both(fallback_on: None) -> None:
    lg, lg_log, ag, ag_log = await run_both(
        ["walk"],
        [],
        {"walk": ok(CapabilityName.WALK), "general": ok(CapabilityName.GENERAL)},
        context={},
    )
    assert lg_log == ag_log == []
    assert lg.status == ag.status == AssistantStatus.CLARIFY
    assert_equivalent(lg, ag)


async def test_clarify_gate_runs_nothing_in_both_even_when_one_intent_could_answer() -> None:
    """계약 정렬의 핵심 — 훈련은 답할 수 있어도 산책 좌표가 없으면 둘 다 아무것도 안 돌린다."""
    lg, lg_log, ag, ag_log = await run_both(
        ["training", "walk"],
        [],
        {"training": ok(CapabilityName.TRAINING), "walk": ok(CapabilityName.WALK)},
        context={},
    )
    assert lg_log == ag_log == []
    assert lg.status == ag.status == AssistantStatus.CLARIFY
    assert lg.clarify is not None and lg.clarify.missing == ["location.lat", "location.lon"]
    assert_equivalent(lg, ag)
