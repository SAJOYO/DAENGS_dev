"""LangChain 에이전트 오케스트레이터.

**`importorskip` 이 맨 위에 있는 이유**: CI 는 `uv sync --frozen --extra place` 하나만
돌려서 `agent` extra 를 안 깝니다 (`.github/workflows/backend-tests.yml`). 감싸지 않으면
수집 단계에서 죽어 **스위트가 통째로 안 돕니다** — `tests/place` 가 겪은 그것입니다.
`ml`·`gait`·`screening` 과 같은 자리입니다.

여기서 재는 것은 **결정론적인 부분**입니다. 모델이 무엇을 고르느냐는 카드 ③의 벤치마크가
재고, 이 파일은 "고른 뒤에 우리가 하는 일"이 계약을 지키는지만 봅니다.
"""

from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("langchain")

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from daengs_backend.orchestration.agent.service import (
    AGENT_MODEL_ID,
    AGENT_PROMPT_VERSION,
    AgentOrchestrationService,
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
from daengs_backend.orchestration.social import social_message

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
        capability=capability,
        status=CapabilityStatus.OK,
        data={"answer": answer},
        elapsed_ms=1,
    )


def toolbox(adapters: dict[CapabilityName, FakeAdapter], **kwargs) -> CapabilityToolbox:
    return CapabilityToolbox(
        query=kwargs.pop("query", "우리 강아지 짖음 어떻게 고쳐?"),
        context=kwargs.pop("context", dict(SEOUL)),
        request_id=kwargs.pop("request_id", "rid-1"),
        adapters=adapters,
    )


class ScriptedChatModel(BaseChatModel):
    """정해진 순서로 답하는 모델. 툴 선택을 테스트가 쥐기 위한 것이다."""

    script: list[AIMessage] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        message = self.script.pop(0) if self.script else AIMessage(content="마쳤습니다.")
        return ChatResult(generations=[ChatGeneration(message=message)])


def calls(*names: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": {}, "id": f"call-{index}"}
            for index, name in enumerate(names)
        ],
    )


def service(script: list[AIMessage], adapters: dict[CapabilityName, FakeAdapter]):
    return AgentOrchestrationService(
        model=ScriptedChatModel(script=list(script)),
        toolbox_factory=lambda **kwargs: CapabilityToolbox(**kwargs, adapters=adapters),
    )


# ── 툴박스: LLM 없이 결정되는 것들 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_payload_comes_from_trusted_context_not_the_model() -> None:
    """툴에 인자가 없다 — payload 는 신뢰된 query·context 에서만 만들어진다 (D-051).

    이게 깨지면 모델이 좌표를 지어낼 수 있고, 그건 엉뚱한 동네를 자신 있게 답하는
    길입니다. LangGraph 쪽 `planner._payload_for` 와 **같은 함수**를 씁니다.
    """
    adapters = {
        CapabilityName.PLACE: FakeAdapter(CapabilityName.PLACE, ok(CapabilityName.PLACE)),
    }
    box = toolbox(adapters, query="근처 애견카페", context=dict(SEOUL))
    tool = next(t for t in box.as_tools() if t.name == "search_places")

    assert tool.args == {}  # 모델이 채울 자리가 없다
    await tool.ainvoke({})

    payload = adapters[CapabilityName.PLACE].calls[0]
    assert isinstance(payload, PlacePayload)
    assert (payload.query, payload.lat, payload.lon) == ("근처 애견카페", 37.5, 127.0)


@pytest.mark.asyncio
async def test_invoked_capabilities_are_recorded() -> None:
    """카드 ③이 골드와 맞댈 축. 여기서 안 남기면 잴 것이 없습니다."""
    adapters = {
        CapabilityName.TRAINING: FakeAdapter(
            CapabilityName.TRAINING, ok(CapabilityName.TRAINING)
        ),
        CapabilityName.PLACE: FakeAdapter(CapabilityName.PLACE, ok(CapabilityName.PLACE)),
    }
    box = toolbox(adapters)
    for name in ("ask_training", "search_places"):
        await next(t for t in box.as_tools() if t.name == name).ainvoke({})

    assert box.called_capabilities == {CapabilityName.TRAINING, CapabilityName.PLACE}
    assert [r.capability for r in box.results] == [
        CapabilityName.TRAINING,
        CapabilityName.PLACE,
    ]


@pytest.mark.asyncio
async def test_same_capability_twice_runs_once() -> None:
    """모델이 같은 툴을 또 불러도 어댑터는 한 번만 돈다.

    두 번 돌면 답이 두 줄로 나가고 비용도 두 배입니다. 거절하지 않고 앞선 관찰을
    다시 주는 이유는, 거절하면 모델이 다른 툴을 찾아 헤매기 때문입니다.
    """
    adapter = FakeAdapter(CapabilityName.TRAINING, ok(CapabilityName.TRAINING))
    box = toolbox({CapabilityName.TRAINING: adapter})
    tool = next(t for t in box.as_tools() if t.name == "ask_training")

    first = await tool.ainvoke({})
    second = await tool.ainvoke({})

    assert len(adapter.calls) == 1
    assert len(box.results) == 1
    assert "이미 실행" in second and "이미 실행" not in first


@pytest.mark.asyncio
async def test_adapter_failure_is_contained_as_error() -> None:
    """한 어댑터의 예외가 요청 전체를 죽이지 않는다 (`graph.py` 와 같은 봉쇄)."""
    adapters = {
        CapabilityName.TRAINING: FakeAdapter(CapabilityName.TRAINING, RuntimeError("boom")),
        CapabilityName.LIFE: FakeAdapter(CapabilityName.LIFE, ok(CapabilityName.LIFE)),
    }
    box = toolbox(adapters)
    for name in ("ask_training", "ask_life"):
        await next(t for t in box.as_tools() if t.name == name).ainvoke({})

    assert box.results[0].status == CapabilityStatus.ERROR
    assert box.results[0].error is not None
    assert "boom" not in box.results[0].error.detail  # 내부 메시지는 안 샌다
    assert box.results[1].status == CapabilityStatus.OK


@pytest.mark.asyncio
async def test_missing_coordinates_block_and_record_clarify() -> None:
    """좌표가 없으면 실행하지 않고, 물어볼 것을 남긴다. 문구는 planner 의 것이다."""
    adapters = {CapabilityName.WALK: FakeAdapter(CapabilityName.WALK, ok(CapabilityName.WALK))}
    box = toolbox(adapters, context={})
    await next(t for t in box.as_tools() if t.name == "check_walk_conditions").ainvoke({})

    assert adapters[CapabilityName.WALK].calls == []
    assert box.results == []
    assert box.clarify is not None
    assert box.clarify.missing == ["location.lat", "location.lon"]


@pytest.mark.asyncio
async def test_two_blocked_capabilities_ask_once_together() -> None:
    """산책과 장소가 둘 다 막히면 한 번에 묻는다 — planner 가 선택 전체를 게이트하는 것과 같다."""
    box = toolbox({}, context={})
    for name in ("check_walk_conditions", "search_places"):
        await next(t for t in box.as_tools() if t.name == name).ainvoke({})

    assert box.clarify is not None
    assert "산책 조건과 주변 장소" in box.clarify.question


@pytest.mark.asyncio
async def test_handoff_tool_records_target_and_reason() -> None:
    box = toolbox({})
    await next(t for t in box.as_tools() if t.name == "hand_off_to_gait").ainvoke({})

    assert [(h.target, h.reason) for h in box.handoffs] == [("gait", "video_upload_required")]


# ── 서비스: 계약을 지키는가 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_results_become_an_answered_response() -> None:
    adapters = {
        CapabilityName.TRAINING: FakeAdapter(
            CapabilityName.TRAINING, ok(CapabilityName.TRAINING, "천천히 무시해 보세요.")
        )
    }
    response = await service([calls("ask_training")], adapters).run(
        query="짖음 어떻게 고쳐?", principal=PRINCIPAL, context=dict(SEOUL)
    )

    assert response.status == AssistantStatus.ANSWERED
    assert response.message == "천천히 무시해 보세요."
    assert [r.capability for r in response.results] == [CapabilityName.TRAINING]


@pytest.mark.asyncio
async def test_route_trace_names_the_agent_not_the_router() -> None:
    """콘솔이 두 구현을 구분하는 유일한 자리 (#238)."""
    adapters = {
        CapabilityName.TRAINING: FakeAdapter(
            CapabilityName.TRAINING, ok(CapabilityName.TRAINING)
        )
    }
    response = await service([calls("ask_training")], adapters).run(
        query="짖음", principal=PRINCIPAL, context=dict(SEOUL), include_route_trace=True
    )

    assert response.route is not None
    assert response.route.router == RouterKind.LLM
    assert response.route.model == AGENT_MODEL_ID
    assert response.route.prompt_version == AGENT_PROMPT_VERSION


@pytest.mark.asyncio
async def test_route_trace_is_absent_by_default() -> None:
    """`include_route_trace` 를 안 넘긴 호출에는 라우팅 메타데이터가 안 실린다."""
    adapters = {
        CapabilityName.TRAINING: FakeAdapter(
            CapabilityName.TRAINING, ok(CapabilityName.TRAINING)
        )
    }
    response = await service([calls("ask_training")], adapters).run(
        query="짖음", principal=PRINCIPAL, context=dict(SEOUL)
    )
    assert response.route is None


@pytest.mark.asyncio
async def test_social_reply_uses_the_shared_template() -> None:
    """모델이 지은 인사말을 내보내지 않는다 — 스몰토크 문구는 비교하려는 축이 아니다."""
    response = await service(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "reply_socially", "args": {"intent": "greeting"}, "id": "c0"}
                ],
            )
        ],
        {},
    ).run(query="안녕", principal=PRINCIPAL, context=dict(SEOUL))

    assert response.status == AssistantStatus.ANSWERED
    assert response.message == social_message("greeting")
    assert response.results == []


@pytest.mark.asyncio
async def test_model_failure_is_failed_never_clarify() -> None:
    """O-14. 모델의 잘못된 출력은 사용자에게 안 보인다."""

    class Exploding(ScriptedChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("provider down")

    response = await AgentOrchestrationService(model=Exploding()).run(
        query="짖음", principal=PRINCIPAL, context=dict(SEOUL)
    )

    assert response.status == AssistantStatus.FAILED
    assert response.clarify is None
    assert response.results == []
    assert "provider down" not in response.message


@pytest.mark.asyncio
async def test_no_capability_and_no_answer_is_failed() -> None:
    """툴을 하나도 안 부르면 답이 아니다.

    모델이 자기 지식으로 답해 버리는 것을 막는 마지막 그물입니다 — v1 이 일부러
    라우팅하지 않기로 한 일반 육아 질문이 여기로 옵니다 (semantic.py v5·v6).
    """
    response = await service([AIMessage(content="하루 두 번이 좋습니다.")], {}).run(
        query="산책 몇 번 시켜야 해?", principal=PRINCIPAL, context=dict(SEOUL)
    )

    assert response.status == AssistantStatus.FAILED
    assert "하루 두 번" not in response.message


@pytest.mark.asyncio
async def test_blocked_coordinates_become_clarify() -> None:
    response = await service([calls("check_walk_conditions")], {}).run(
        query="지금 산책해도 돼?", principal=PRINCIPAL, context={}
    )

    assert response.status == AssistantStatus.CLARIFY
    assert response.clarify is not None
    assert response.results == []


@pytest.mark.asyncio
async def test_an_answer_beats_a_blocked_capability() -> None:
    """이미 나온 답을 버리고 되묻지는 않는다 — 두 구현이 갈리는 유일한 계약 지점."""
    adapters = {
        CapabilityName.TRAINING: FakeAdapter(
            CapabilityName.TRAINING, ok(CapabilityName.TRAINING, "훈련 답")
        )
    }
    response = await service(
        [calls("ask_training", "check_walk_conditions")], adapters
    ).run(query="짖음 고치고 지금 산책해도 돼?", principal=PRINCIPAL, context={})

    assert response.status == AssistantStatus.ANSWERED
    assert response.clarify is None
    assert "훈련 답" in response.message


@pytest.mark.asyncio
async def test_explicit_signal_skips_the_agent() -> None:
    """명시 신호는 두 구현에서 똑같이 동작한다 (D-036).

    이미 무엇을 부를지 정해 준 요청에 모델 비용을 붙이지 않습니다. 비교는 의미
    경로에서만 뜻이 있습니다.
    """
    adapters = {CapabilityName.LIFE: FakeAdapter(CapabilityName.LIFE, ok(CapabilityName.LIFE))}
    model = ScriptedChatModel(script=[calls("ask_training")])  # 불리면 안 된다
    response = await AgentOrchestrationService(
        model=model,
        toolbox_factory=lambda **kwargs: CapabilityToolbox(**kwargs, adapters=adapters),
    ).run(
        query="등록 어떻게 해?",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        requested_capability="life",
        include_route_trace=True,
    )

    assert model.script  # 대본이 안 소비됐다 = 모델을 안 불렀다
    assert [r.capability for r in response.results] == [CapabilityName.LIFE]
    assert response.route is not None
    assert response.route.router == RouterKind.DETERMINISTIC


@pytest.mark.asyncio
async def test_raw_credentials_in_context_are_rejected() -> None:
    """`graph.py` 와 같은 보호. 실험 구현이 구멍이 되면 안 된다."""
    with pytest.raises(ValueError, match="credentials"):
        await service([], {}).run(
            query="짖음",
            principal=PRINCIPAL,
            context={"nested": {"authorization": "Bearer x"}},
        )


@pytest.mark.asyncio
async def test_locale_other_than_korean_is_rejected() -> None:
    with pytest.raises(ValueError, match="ko-KR"):
        await service([], {}).run(query="q", principal=PRINCIPAL, locale="en-US")


@pytest.mark.asyncio
async def test_payloads_match_the_langgraph_shapes() -> None:
    """두 구현이 같은 능력을 같은 입력으로 부른다. 아니면 비교가 payload 비교가 된다."""
    adapters = {
        name: FakeAdapter(name, ok(name))
        for name in (
            CapabilityName.TRAINING,
            CapabilityName.LIFE,
            CapabilityName.WALK,
            CapabilityName.PLACE,
        )
    }
    box = toolbox(adapters, query="질문", context={**SEOUL, "dog": {"breed": "퍼그"}})
    for name in ("ask_training", "ask_life", "check_walk_conditions", "search_places"):
        await next(t for t in box.as_tools() if t.name == name).ainvoke({})

    assert adapters[CapabilityName.TRAINING].calls[0] == TrainingPayload(question="질문")
    life = adapters[CapabilityName.LIFE].calls[0]
    assert isinstance(life, LifePayload) and life.dog is not None and life.dog.breed == "퍼그"
    assert adapters[CapabilityName.WALK].calls[0] == WalkPayload(lat=37.5, lon=127.0)
    assert adapters[CapabilityName.PLACE].calls[0] == PlacePayload(
        query="질문", lat=37.5, lon=127.0
    )


@pytest.mark.asyncio
async def test_results_survive_a_mid_loop_model_failure() -> None:
    """루프 중간에 모델이 죽어도 **이미 나온 답은 버리지 않는다.**

    의미 라우터는 실행 *전에* 실패하므로 잃을 것이 없지만, 에이전트는 도구를 부른 뒤에
    죽을 수 있습니다. 그 요청까지 FAILED 로 돌려보내면 멀쩡히 돈 능력의 결과를 우리가
    지우는 것이 됩니다. 두 구현이 갈리는 두 번째 지점이고, 카드 ③은 알고 채점해야 합니다.
    """

    class FailAfterToolCall(ScriptedChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if self.script:
                return super()._generate(messages, stop, run_manager, **kwargs)
            raise RuntimeError("provider down")

    adapters = {
        CapabilityName.TRAINING: FakeAdapter(
            CapabilityName.TRAINING, ok(CapabilityName.TRAINING, "훈련 답")
        )
    }
    response = await AgentOrchestrationService(
        model=FailAfterToolCall(script=[calls("ask_training")]),
        toolbox_factory=lambda **kwargs: CapabilityToolbox(**kwargs, adapters=adapters),
    ).run(query="짖음", principal=PRINCIPAL, context=dict(SEOUL))

    assert response.status == AssistantStatus.ANSWERED
    assert response.message == "훈련 답"
    assert "provider down" not in response.message


@pytest.mark.asyncio
async def test_trace_metadata_keys_match_langgraph() -> None:
    """두 구현의 트레이스를 **같은 쿼리로 걸러야** 비교가 된다 (D-054).

    키가 갈리면 카드 ③이 재려는 지연·토큰 비용이 한쪽에서만 나옵니다 — 에이전트가
    루프를 돌아 비싼지가 이 실험의 주된 발견이 될 수 있는데 그걸 못 재게 됩니다.
    `graph.py` 가 키를 늘리면 여기가 먼저 깨져서 알려 줍니다.
    """
    import re

    config = AgentOrchestrationService._trace_config(
        request_id="11111111-1111-1111-1111-111111111111", principal=PRINCIPAL
    )
    graph_source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src/daengs_backend/orchestration/graph.py"
    ).read_text(encoding="utf-8")
    graph_block = graph_source.split("metadata={", 1)[1].split("},", 1)[0]
    graph_keys = set(re.findall(r'"(\w+)":', graph_block))

    assert graph_keys, "graph.py 의 trace metadata 를 못 읽었습니다 — 이 테스트를 고치세요"
    assert graph_keys <= set(config["metadata"])
    assert config["metadata"]["router_model"] == AGENT_MODEL_ID
    assert config["metadata"]["prompt_version"] == AGENT_PROMPT_VERSION
    # run_id 가 request_id 여야 신고 → 트레이스 링크가 산다 (D-054).
    assert str(config["run_id"]) == "11111111-1111-1111-1111-111111111111"
    # run_name 만 다르다 — 트레이스에서 두 구현을 가르는 자리.
    assert config["run_name"] == "assistant_query_agent"
    assert '"assistant_query"' in graph_source
