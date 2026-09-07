"""LangChain 에이전트 오케스트레이터 — 동결 계약에 맞춘 v2 (#272).

**`importorskip` 이 맨 위에 있는 이유**: `agent` extra 는 기본 설치에 없습니다. CI 는 D-055 ⑦
부터 그것을 깔아 이 파일이 실제로 돌지만, extra 없는 개발 PC 에서는 감싸지 않으면 수집
단계에서 죽어 **스위트가 통째로 안 돕니다** — `tests/place` 가 겪은 그것입니다.
`ml`·`gait`·`screening` 과 같은 자리입니다.

여기서 재는 것은 **결정론적인 부분**입니다. 모델이 무엇을 고르느냐는 비교 벤치마크가
재고, 이 파일은 "고른 뒤에 우리가 하는 일"이 LangGraph 와 같은 계약을 지키는지만 봅니다:
CLARIFY 는 배타이고 어댑터는 전역 검증 전에 돌지 않는다.
"""

from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("langchain")

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, SecretStr

from daengs_backend.config import settings
from daengs_backend.orchestration.agent.service import (
    AGENT_MAX_RETRIES,
    AGENT_MODEL_ID,
    AGENT_PROMPT_VERSION,
    AgentOrchestrationService,
    build_agent_model,
    build_agent_user_message,
)
from daengs_backend.orchestration.agent.tools import CapabilityToolbox
from daengs_backend.orchestration.contracts import (
    AssistantStatus,
    CapabilityName,
    CapabilityResult,
    CapabilityStatus,
    LifePayload,
    PlacePayload,
    PrincipalContext,
    RouterKind,
    TrainingPayload,
    WalkPayload,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.planner import assemble_route_plan
from daengs_backend.orchestration.semantic import (
    ROUTER_CANDIDATE_COUNT,
    ROUTER_MAX_OUTPUT_TOKENS,
    ROUTER_MODEL_ID,
    ROUTER_TEMPERATURE,
    SemanticRoutingDecision,
    build_semantic_router_prompt,
)
from daengs_backend.orchestration.service import _ROUTER_FAILURE_MESSAGE
from daengs_backend.orchestration.social import social_message
from tools.router_benchmark.evaluate import _semantic_plan_key

PRINCIPAL = PrincipalContext(subject="test-user", kind="APP_USER")
SEOUL = {"location": {"lat": 37.5, "lon": 127.0}}


class FakeAdapter:
    """어댑터 자리를 대신한다. 도메인은 이 카드의 관심이 아니다."""

    def __init__(self, capability: CapabilityName, result: CapabilityResult | Exception) -> None:
        self.capability = capability
        self._result = result
        self.calls: list[object] = []

    async def run(self, request, *, request_id: str) -> CapabilityResult:
        self.calls.append(request.payload)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def ok(capability: CapabilityName, answer: str = "답") -> CapabilityResult:
    return CapabilityResult(
        capability=capability, status=CapabilityStatus.OK, data={"answer": answer}, elapsed_ms=1
    )


def adapters(*names: CapabilityName) -> dict[CapabilityName, FakeAdapter]:
    return {name: FakeAdapter(name, ok(name, f"{name.value} 답")) for name in names}


ALL = (CapabilityName.TRAINING, CapabilityName.LIFE, CapabilityName.WALK, CapabilityName.PLACE)


class ScriptedChatModel(BaseChatModel):
    """정해진 순서로 답하는 모델. 툴 선택을 테스트가 쥐기 위한 것이다."""

    script: list[AIMessage] = Field(default_factory=list)
    seen: list[list] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        message = self.script.pop(0) if self.script else AIMessage(content="마쳤습니다.")
        return ChatResult(generations=[ChatGeneration(message=message)])


def calls(*names: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": {}, "id": f"call-{index}"} for index, name in enumerate(names)
        ],
    )


def service(
    script: list[AIMessage], fakes: dict[CapabilityName, FakeAdapter] | None = None
) -> AgentOrchestrationService:
    return AgentOrchestrationService(
        model=ScriptedChatModel(script=list(script)),
        engine=OrchestrationEngine(fakes or {}),
    )


async def tool(box: CapabilityToolbox, name: str, **args):
    return await next(t for t in box.as_tools() if t.name == name).ainvoke(args)


# ── 툴박스: 선택만 기록한다 ─────────────────────────────────────────────


def test_tools_take_no_arguments_except_the_social_intent() -> None:
    """모델이 payload 를 쓸 자리가 없다 (D-051). 인사 툴의 intent 만 분류값으로 받는다."""
    by_name = {t.name: t for t in CapabilityToolbox().as_tools()}
    assert set(by_name) == {
        "ask_training",
        "ask_life",
        "check_walk_conditions",
        "search_places",
        "answer_generally",  # D-057 ① — mirrors the router's v9 `general` destination
        "hand_off_to_skin",
        "hand_off_to_gait",
        "reply_socially",
    }
    for name, t in by_name.items():
        assert t.args == (
            {
                "intent": {
                    "title": "Intent",
                    "type": "string",
                    "enum": ["greeting", "thanks", "goodbye"],
                }
            }
            if name == "reply_socially"
            else {}
        ), name


async def test_tool_calls_record_a_selection_and_execute_nothing() -> None:
    """툴에는 어댑터가 없다. 부르면 결정에 이름만 쌓인다 — 실행은 다른 곳의 일이다."""
    box = CapabilityToolbox()
    observation = await tool(box, "search_places")
    await tool(box, "ask_training")
    await tool(box, "hand_off_to_gait")

    assert box.execute == ["place", "training"]  # 부른 순서. 실행 순서는 planner 가 정한다
    assert box.handoffs == ["gait"]
    assert "기록" in observation and "실행" in observation


async def test_same_capability_twice_is_recorded_once() -> None:
    box = CapabilityToolbox()
    first = await tool(box, "ask_training")
    second = await tool(box, "ask_training")
    await tool(box, "hand_off_to_skin")
    await tool(box, "hand_off_to_skin")

    assert box.execute == ["training"] and box.handoffs == ["skin"]
    assert "이미 기록" in second and "이미 기록" not in first


async def test_decision_lets_capability_intent_beat_social_intent() -> None:
    """라우터 스키마의 `social_intent_is_exclusive` 와 같은 규칙. 능력이 있으면 인사는 버린다."""
    box = CapabilityToolbox()
    await tool(box, "reply_socially", intent="greeting")
    assert box.decision() == SemanticRoutingDecision(social_intent="greeting")

    await tool(box, "ask_life")
    assert box.decision() == SemanticRoutingDecision(execute=["life"], social_intent=None)


# ── 서비스: LangGraph 와 같은 CLARIFY 계약 ──────────────────────────────


@pytest.mark.parametrize(
    ("script", "context", "missing", "question"),
    [
        (
            calls("ask_training", "check_walk_conditions"),
            {},
            ["location.lat", "location.lon"],
            "산책할 위치의 위도와 경도를 알려주세요.",
        ),
        (
            calls("ask_life", "check_walk_conditions"),
            {},
            ["location.lat", "location.lon"],
            "산책할 위치의 위도와 경도를 알려주세요.",
        ),
        (
            calls("ask_training", "check_walk_conditions"),
            {"location": {"lon": 127.0}},
            ["location.lat"],
            "현재 위치의 위도를 알려주세요.",
        ),
        (
            calls("check_walk_conditions", "search_places"),
            {},
            ["location.lat", "location.lon"],
            "지금 위치의 위도와 경도를 알려주시면 산책 조건과 주변 장소를 함께 찾아볼게요.",
        ),
    ],
    ids=["training+walk", "life+walk", "training+walk-one-coordinate", "walk+place"],
)
async def test_missing_coordinates_make_the_whole_selection_an_exclusive_clarify(
    script, context, missing, question
) -> None:
    """이미 답할 수 있던 의도(훈련·생활)도 **먼저 실행되지 않는다.** v1 이 갈린 자리다.

    선택 전체가 planner 의 한 게이트를 지나므로 좌표가 하나라도 없으면 아무 어댑터도 돌지 않고,
    되묻는 문구는 무엇이 빠졌는지 말한다. 문구는 planner 의 것이라 LangGraph 와 글자까지 같다.
    """
    fakes = adapters(*ALL)
    response = await service([script], fakes).run(query="q", principal=PRINCIPAL, context=context)

    assert response.status == AssistantStatus.CLARIFY
    assert response.clarify is not None
    assert response.clarify.missing == missing
    assert response.message == question
    assert response.results == [] and response.handoffs == []
    assert all(fake.calls == [] for fake in fakes.values())


async def test_clarify_also_suppresses_a_selected_handoff() -> None:
    """핸드오프도 CLARIFY 앞에서는 나가지 않는다 — 배타 (O-8). LangGraph 와 같다."""
    fakes = adapters(*ALL)
    response = await service([calls("hand_off_to_gait", "check_walk_conditions")], fakes).run(
        query="q", principal=PRINCIPAL, context={}
    )
    assert response.status == AssistantStatus.CLARIFY
    assert response.handoffs == []
    assert all(fake.calls == [] for fake in fakes.values())


async def test_a_complete_selection_executes_in_planner_order() -> None:
    """모델이 산책을 먼저 골라도 실행은 planner 의 순서(훈련 → 생활 → 산책 → 장소)다.

    사용자에게 보이는 섹션 순서가 모델의 변덕에 따라 바뀌면 안 된다 (`planner._EXECUTION_ORDER`).
    """
    fakes = adapters(*ALL)
    response = await service(
        [calls("search_places", "check_walk_conditions", "ask_training")], fakes
    ).run(query="q", principal=PRINCIPAL, context=dict(SEOUL))
    assert response.status == AssistantStatus.ANSWERED
    assert [r.capability for r in response.results] == [
        CapabilityName.TRAINING,
        CapabilityName.WALK,
        CapabilityName.PLACE,
    ]
    assert fakes[CapabilityName.LIFE].calls == []


async def test_selection_spread_over_turns_is_one_plan() -> None:
    """루프가 여러 턴에 걸쳐 툴을 더해도 계획은 하나이고 실행은 루프가 끝난 뒤다."""
    fakes = adapters(*ALL)
    response = await service([calls("ask_training"), calls("hand_off_to_gait")], fakes).run(
        query="q", principal=PRINCIPAL, context=dict(SEOUL)
    )
    assert response.status == AssistantStatus.ANSWERED
    assert [r.capability for r in response.results] == [CapabilityName.TRAINING]
    assert [h.target for h in response.handoffs] == ["gait"]


async def test_payloads_come_from_trusted_context_and_match_the_langgraph_shapes() -> None:
    """두 구현이 같은 능력을 같은 입력으로 부른다 — 같은 `planner._payload_for` 를 지나므로."""
    fakes = adapters(*ALL)
    await service(
        [calls("ask_training", "ask_life", "check_walk_conditions", "search_places")], fakes
    ).run(query="질문", principal=PRINCIPAL, context={**SEOUL, "dog": {"breed": "퍼그"}})
    assert fakes[CapabilityName.TRAINING].calls == [TrainingPayload(question="질문")]
    life = fakes[CapabilityName.LIFE].calls[0]
    assert isinstance(life, LifePayload) and life.dog is not None and life.dog.breed == "퍼그"
    assert fakes[CapabilityName.WALK].calls == [WalkPayload(lat=37.5, lon=127.0)]
    assert fakes[CapabilityName.PLACE].calls == [PlacePayload(query="질문", lat=37.5, lon=127.0)]


async def test_same_selection_yields_the_same_plan_as_the_semantic_path() -> None:
    """에이전트가 고른 것과 라우터가 고른 것이 같으면 RoutePlan 도 같다 (채점기의 의미 키로)."""
    fakes = adapters(*ALL)
    engine = OrchestrationEngine(fakes)
    seen: list = []
    real = engine.run

    async def spy(*, route_plan, **kwargs):
        seen.append(route_plan)
        return await real(route_plan=route_plan, **kwargs)

    engine.run = spy  # type: ignore[method-assign]
    await AgentOrchestrationService(
        model=ScriptedChatModel(script=[calls("check_walk_conditions", "ask_training")]),
        engine=engine,
    ).run(query="짖음 고치고 산책 돼?", principal=PRINCIPAL, context=dict(SEOUL))

    langgraph_plan = assemble_route_plan(
        SemanticRoutingDecision(execute=["training", "walk"]),
        query="짖음 고치고 산책 돼?",
        context=dict(SEOUL),
        router=RouterKind.LLM,
    )
    assert _semantic_plan_key(seen[0]) == _semantic_plan_key(langgraph_plan)
    assert seen[0].model == AGENT_MODEL_ID and seen[0].prompt_version == AGENT_PROMPT_VERSION


async def test_tool_results_become_an_answered_response() -> None:
    fakes = adapters(CapabilityName.TRAINING)
    response = await service([calls("ask_training")], fakes).run(
        query="짖음 어떻게 고쳐?", principal=PRINCIPAL, context=dict(SEOUL)
    )
    assert response.status == AssistantStatus.ANSWERED
    assert response.message == "training 답"


async def test_route_trace_names_the_agent_not_the_router() -> None:
    """콘솔이 두 구현을 구분하는 유일한 자리 (#238). 엔진이 RoutePlan 의 메타데이터를 옮긴다."""
    response = await service([calls("ask_training")], adapters(CapabilityName.TRAINING)).run(
        query="짖음", principal=PRINCIPAL, context=dict(SEOUL), include_route_trace=True
    )
    assert response.route is not None
    assert response.route.router == RouterKind.LLM
    assert response.route.model == AGENT_MODEL_ID
    assert response.route.prompt_version == AGENT_PROMPT_VERSION


async def test_route_trace_is_absent_by_default() -> None:
    response = await service([calls("ask_training")], adapters(CapabilityName.TRAINING)).run(
        query="짖음", principal=PRINCIPAL, context=dict(SEOUL)
    )
    assert response.route is None


async def test_social_reply_uses_the_shared_template() -> None:
    """모델이 지은 인사말을 내보내지 않는다 — 스몰토크 문구는 비교하려는 축이 아니다."""
    greeting = AIMessage(
        content="",
        tool_calls=[{"name": "reply_socially", "args": {"intent": "greeting"}, "id": "c0"}],
    )
    response = await service([greeting]).run(query="안녕", principal=PRINCIPAL, context=dict(SEOUL))
    assert response.status == AssistantStatus.ANSWERED
    assert response.message == social_message("greeting")
    assert response.results == []


async def test_model_failure_before_selection_is_failed_never_clarify() -> None:
    """O-14. 라우터 실패와 같은 문구, 어댑터 0회. 모델의 잘못된 출력은 사용자에게 안 보인다."""

    class Exploding(ScriptedChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("provider down")

    fakes = adapters(*ALL)
    response = await AgentOrchestrationService(
        model=Exploding(), engine=OrchestrationEngine(fakes)
    ).run(query="짖음", principal=PRINCIPAL, context=dict(SEOUL))
    assert response.status == AssistantStatus.FAILED
    assert response.message == _ROUTER_FAILURE_MESSAGE
    assert response.clarify is None and response.results == []
    assert all(fake.calls == [] for fake in fakes.values())


async def test_model_failure_mid_loop_freezes_no_plan_and_runs_nothing() -> None:
    """툴을 고른 뒤 마무리 턴에서 죽어도 계획은 동결되지 않았다 — 어댑터 0회, FAILED.

    v1 은 이 자리에서 이미 나온 훈련 답을 살렸다. v2 에서는 살릴 결과가 애초에 없다(실행이
    선택 뒤이므로). 부분 선택을 계획으로 승격하지 않는 것이 공유 라우터 실패 계약이다.
    """

    class FailAfterToolCall(ScriptedChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if self.script:
                return super()._generate(messages, stop, run_manager, **kwargs)
            raise RuntimeError("provider down")

    fakes = adapters(*ALL)
    response = await AgentOrchestrationService(
        model=FailAfterToolCall(script=[calls("ask_training")]), engine=OrchestrationEngine(fakes)
    ).run(query="짖음", principal=PRINCIPAL, context=dict(SEOUL))
    assert response.status == AssistantStatus.FAILED
    assert response.message == _ROUTER_FAILURE_MESSAGE
    assert fakes[CapabilityName.TRAINING].calls == []


async def test_no_capability_and_no_answer_is_failed() -> None:
    """툴을 하나도 안 부르면 답이 아니다 — 라우터가 빈 결정을 낸 것과 같은 길로 FAILED 다.

    모델이 자기 지식으로 답해 버리는 것을 막는 마지막 그물입니다 — v1 이 일부러
    라우팅하지 않기로 한 일반 육아 질문이 여기로 옵니다 (semantic.py v5·v6).
    """
    response = await service([AIMessage(content="하루 두 번이 좋습니다.")]).run(
        query="산책 몇 번 시켜야 해?", principal=PRINCIPAL, context=dict(SEOUL)
    )
    assert response.status == AssistantStatus.FAILED
    assert "하루 두 번" not in response.message


async def test_explicit_signal_skips_the_agent() -> None:
    """명시 신호는 두 구현에서 똑같이 동작한다 (D-036) — 같은 결정론 경로, 같은 엔진."""
    fakes = adapters(CapabilityName.LIFE)
    model = ScriptedChatModel(script=[calls("ask_training")])  # 불리면 안 된다
    response = await AgentOrchestrationService(model=model, engine=OrchestrationEngine(fakes)).run(
        query="등록 어떻게 해?",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        requested_capability="life",
        include_route_trace=True,
    )
    assert model.script  # 대본이 안 소비됐다 = 모델을 안 불렀다
    assert [r.capability for r in response.results] == [CapabilityName.LIFE]
    assert response.route is not None and response.route.router == RouterKind.DETERMINISTIC


async def test_raw_credentials_in_context_are_rejected_before_the_model_runs() -> None:
    model = ScriptedChatModel(script=[calls("ask_training")])
    with pytest.raises(ValueError, match="credentials"):
        await AgentOrchestrationService(model=model, engine=OrchestrationEngine({})).run(
            query="짖음", principal=PRINCIPAL, context={"nested": {"authorization": "Bearer x"}}
        )
    assert model.seen == []


async def test_locale_other_than_korean_is_rejected() -> None:
    with pytest.raises(ValueError, match="ko-KR"):
        await service([]).run(query="q", principal=PRINCIPAL, locale="en-US")


# ── 입력 표면: 라우터와 같은 것을 본다 ──────────────────────────────────


async def test_routing_metadata_reaches_the_model_like_the_router_prompt() -> None:
    """라우터가 보는 `ROUTING_METADATA` 를 에이전트도 본다. 좌표는 여전히 어느 쪽도 못 본다."""
    model = ScriptedChatModel(script=[calls("ask_training")])
    context = {**SEOUL, "source": "assistant_chat", "action": "check_walk"}
    await AgentOrchestrationService(model=model, engine=OrchestrationEngine(adapters(*ALL))).run(
        query="짖음", principal=PRINCIPAL, context=context
    )
    human = next(m for m in model.seen[0] if isinstance(m, HumanMessage))
    assert human.content == build_agent_user_message(query="짖음", context=context)
    assert 'ROUTING_METADATA: {"action": "check_walk", "source": "assistant_chat"}' in human.content
    assert human.content.endswith("USER_QUERY: 짖음")
    assert "37.5" not in human.content

    router_tail = build_semantic_router_prompt(query="짖음", context=context).rsplit(
        "INPUT_LOCALE", 1
    )[1]
    assert human.content in router_tail  # 글자까지 같은 꼬리


@pytest.mark.parametrize("bad", [{"source": ""}, {"action": {"x": 1}}, {"active_dog_id": 3}])
async def test_malformed_routing_metadata_fails_the_same_way_as_the_router(bad: dict) -> None:
    with pytest.raises(ValueError, match="routing metadata"):
        build_semantic_router_prompt(query="q", context=bad)
    model = ScriptedChatModel(script=[calls("ask_training")])
    with pytest.raises(ValueError, match="routing metadata"):
        await AgentOrchestrationService(model=model, engine=OrchestrationEngine({})).run(
            query="q", principal=PRINCIPAL, context=bad
        )
    assert model.seen == []


# ── 통제 설정: 라우터와 같은 모델·같은 값 ─────────────────────────────────


def test_build_agent_model_pins_the_controlled_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """temperature 를 **명시**해야 한다 — `langchain-google-genai` 는 Gemini 3 계열에 안 넘기면
    None(프로바이더 기본)으로 둔다. v1 비교의 에이전트가 그렇게 돌았다."""
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("test-key"))
    model = build_agent_model()

    assert AGENT_MODEL_ID == ROUTER_MODEL_ID and AGENT_MODEL_ID in model.model
    assert "temperature" in model.model_fields_set
    assert model.temperature == ROUTER_TEMPERATURE == 0.0
    assert model.n == ROUTER_CANDIDATE_COUNT == 1
    assert model.max_output_tokens == ROUTER_MAX_OUTPUT_TOKENS == 256
    assert model.max_retries == AGENT_MAX_RETRIES == 0
    assert model.timeout == settings.gemini_timeout_ms / 1_000


def test_default_runtime_model_is_the_controlled_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """운영 후보(`build_orchestrator("agent")` 가 쓰는 기본 모델)와 러너가 같은 함수를 쓴다."""
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("test-key"))
    default = AgentOrchestrationService()._default_model()
    built = build_agent_model()
    for name in ("model", "temperature", "n", "max_output_tokens", "max_retries", "timeout"):
        assert getattr(default, name) == getattr(built, name), name


def test_trace_metadata_keys_match_langgraph() -> None:
    """두 구현의 트레이스를 **같은 쿼리로 걸러야** 비교가 된다 (D-054)."""
    import re

    config = AgentOrchestrationService._trace_config(
        request_id="11111111-1111-1111-1111-111111111111", principal=PRINCIPAL
    )
    graph_source = (
        pathlib.Path(__file__).resolve().parents[1] / "src/daengs_backend/orchestration/graph.py"
    ).read_text(encoding="utf-8")
    graph_block = graph_source.split("metadata={", 1)[1].split("},", 1)[0]
    graph_keys = set(re.findall(r'"(\w+)":', graph_block))

    assert graph_keys, "graph.py 의 trace metadata 를 못 읽었습니다 — 이 테스트를 고치세요"
    assert graph_keys <= set(config["metadata"])
    assert config["metadata"]["router_model"] == AGENT_MODEL_ID
    assert config["metadata"]["prompt_version"] == AGENT_PROMPT_VERSION
    assert str(config["run_id"]) == "11111111-1111-1111-1111-111111111111"
    assert config["run_name"] == "assistant_query_agent"
    assert '"assistant_query"' in graph_source
