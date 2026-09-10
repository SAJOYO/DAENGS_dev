"""LangChain tool-calling 에이전트로 `/assistant/query` 를 답한다.

LangGraph 쪽(`../service.py`)은 의미 라우터의 **구조화 출력 한 번**으로 목적지를 고른다.
여기서는 모델이 능력을 툴로 쥐고 **function calling 루프**를 돌며 고른다. v2(#272)부터
**그것이 유일한 차이다** — 고른 뒤는 전부 공유한다:

- 게이트·payload·순서: 같은 `planner.assemble_route_plan`. 좌표가 하나라도 없으면 선택
  전체가 배타 CLARIFY 가 되고 아무것도 실행되지 않는다 (O-8).
- 실행: 같은 `OrchestrationEngine` · 같은 `adapters/`.
- 집계: 같은 `aggregate_results`. `AssistantResponse` 8상태가 갈리면 비교할 좌표계가 없다.

**v1 과 무엇이 달라졌나.** v1(#247) 의 툴은 부르는 즉시 어댑터를 돌렸고, 그래서 에이전트는
막힌 능력이 있어도 이미 나온 답을 살렸다 — 골드가 동결한 계약과 다른 계약이었고 비교 v1
은 그 차이를 쟀다 (#252). v2 의 툴은 선택만 기록하고(`tools.py`), 루프가 끝난 뒤 그 선택
전체를 한 번에 게이트한다. 그 대가로 에이전트는 툴 결과를 보고 다음 수를 정하지 못한다 —
관찰은 "기록했다" 뿐이다. 비교가 재는 축(선택 · 계약 준수 · 오버헤드)에서는 잃는 것이 없고,
가짜 어댑터로 재는 벤치마크에서는 v1 에서도 그 관찰에 정보가 없었다.

**계획이 동결되기 전의 실패는 O-14 다.** 모델·프로바이더가 루프 어디서 죽든 어댑터는
아직 하나도 안 돌았으므로, 의미 라우터 실패와 같은 문구로 FAILED 를 돌려준다. 부분 선택을
계획으로 승격하지 않는다.

**모델은 자기 지식으로 답하지 않는다.** 툴 결과만이 답의 재료다. 이것을 안 박아 두면
에이전트는 v1 이 일부러 라우팅하지 않기로 한 일반 육아 질문까지 답해 버리고, 그러면
비교가 "오케스트레이션 전략"이 아니라 "안전 정책"의 비교가 된다 (semantic.py v5·v6).

**모델과 생성 설정을 의미 라우터와 맞춘 것도 비교 설계다.** 같은 계열의 모델이라야 결과에서
"에이전트라서 좋아진 것"과 "모델이 달라서 좋아진 것"이 섞이지 않는다. temperature 등은
`semantic.py` 의 상수를 **import 해서** 쓴다 — 숫자를 따로 적으면 같다는 것을 코드가
증명하지 못하고, 실제로 v1 은 temperature 를 명시하지 않아 프로바이더 기본값으로 돌았다
(`langchain-google-genai` 는 Gemini 3 계열에 temperature 를 안 넘기면 None 으로 둔다).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

# **최상단 import 가 맞습니다.** 지연 import 는 `runtime.py` 의 `agent` 갈래 하나에만
# 둡니다. 여기서까지 미루면 extra 없이 `DAENGS_ORCHESTRATOR=agent` 로 뜬 서버가
# 멀쩡히 기동한 뒤 **모든 요청을 "잠시 후 다시 시도해 주세요" 로** 돌려보냅니다 —
# 설치가 빠진 것이 프로바이더 장애처럼 보이는, 이 저장소가 싫어하는 그 모양입니다
# (`/life/ask` 만 503 이던 자리와 같습니다). 여기 두면 구현을 고르는 순간
# ModuleNotFoundError 로 무엇이 없는지 이름을 대고 실패합니다.
from langchain.agents import create_agent
from langchain_google_genai import ChatGoogleGenerativeAI

from daengs_backend.config import settings
from daengs_backend.core.tracing import request_trace, trace_config
from daengs_backend.orchestration.agent.tools import CapabilityToolbox
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    PrincipalContext,
    RouterKind,
    RouteTrace,
)

# `graph.py` 의 자격증명 차단과 `service.py` 의 실패 문구를 그대로 쓴다. 둘 다 두
# 구현이 **같아야** 하는 것이다 — 보호가 한쪽에만 있으면 실험 구현이 구멍이 되고,
# 실패 문구가 갈리면 사용자에게 실패가 두 종류로 보인다. 사본을 만들지 않는다.
from daengs_backend.orchestration.graph import OrchestrationEngine, _reject_raw_credentials
from daengs_backend.orchestration.planner import (
    assemble_route_plan,
    resolve_deterministic_route,
    resolve_emergency_route,
)
from daengs_backend.orchestration.semantic import (
    ROUTER_CANDIDATE_COUNT,
    ROUTER_MAX_OUTPUT_TOKENS,
    ROUTER_MODEL_ID,
    ROUTER_TEMPERATURE,
    SemanticRoutingDecision,
    routing_metadata,
)
from daengs_backend.orchestration.service import _KST, _ROUTER_FAILURE_MESSAGE, _is_night
from daengs_backend.orchestration.social import build_social_response

# 의미 라우터와 **같은 모델**이어야 한다 (모듈 docstring). 별도 이름을 두는 이유는
# 트레이스와 RoutePlan 이 "어느 구현이 골랐나" 를 이 값으로 적기 때문이고, 값이 갈리면
# 아래 `build_agent_model` 을 잰 테스트가 먼저 깨진다.
AGENT_MODEL_ID = ROUTER_MODEL_ID
# v3 (#279): 의미 라우터 v8 의 배제 문장을 거울로 넣고, "답할 수 없다고만" 을 "도구 없이
# 마쳐라 — 일반 답변은 시스템이 붙인다" 로 바꿨다. 다른 문장은 v2 그대로다.
# v4 (D-057 ①): 라우터 v9 의 `general` 목적지를 `answer_generally` 로 거울 — 일반 돌봄은
# 전문 도구에 **더해** 부르고, 반려견과 무관하면 아무 도구도 안 부른다.
AGENT_PROMPT_VERSION = "agent-ko-v4"
# 프로바이더 재시도. 의미 라우터의 `google-genai` 클라이언트는 retry_options 를 안 주어
# 재시도가 없다. `langchain-google-genai` 는 기본 `max_retries=6` 이라 명시로 0 이다 —
# 실패한 호출을 조용히 여섯 번 더 부르면 지연·토큰이 "프로바이더 사정" 에 묻힌다.
AGENT_MAX_RETRIES = 0

_SYSTEM_PROMPT = """당신은 DAENGS 반려견 비서입니다. 한국어로 답합니다.

**당신은 스스로 답하지 않습니다.** 답은 도구가 만듭니다. 도구가 주지 않은 사실을
지어내거나, 당신이 알고 있는 일반 상식으로 채우지 마세요. 진단하지 않습니다.

무엇을 물었는지 보고 필요한 도구를 **모두** 부르세요. 한 발화가 두 가지를 물으면 둘 다
부릅니다. 필요 없는 도구는 부르지 않습니다. 사용자가 한 주제를 분명히 빼 달라고 하면(하나는
말고 다른 하나만) 그 어휘가 문장에 보여도 빠진 쪽 도구는 부르지 않습니다.

**도구는 실행하지 않고 선택을 기록합니다.** 도구를 부르면 "기록했다"는 확인만 돌아오고,
실제 실행은 당신이 선택을 마친 뒤 시스템이 한꺼번에 합니다. 그러니 도구 결과를 기다리거나
그 내용을 옮겨 적을 일이 없습니다.

**사용자의 현재 위치는 시스템이 이미 갖고 있습니다.** 위치를 되묻지 말고 그냥 도구를
부르세요. 위치가 없으면 되묻는 답이 자동으로 나갑니다. 마찬가지로 질문 원문도 시스템이
이미 갖고 있어서, 도구에는 넘길 인자가 없습니다.

사용자 메시지는 `ROUTING_METADATA`(앱이 붙인 라우팅 힌트)와 `USER_QUERY`(사용자 원문)로
옵니다. 답할 대상은 `USER_QUERY` 입니다.

경계:
- 행동을 바꾸거나 가르치는 것 → ask_training
- 제도·법령·행정·정책·계약의 공식 정보 → ask_life. **일반적인 사육·돌봄 조언은 여기가
  아니라 answer_generally 입니다.** 맞는 도구가 하나도 없으면 도구를
  부르지 말고 그냥 마치세요 — 일반 답변은 시스템이 붙입니다.
- 지금 나가도 되는 환경인가 → check_walk_conditions
- 어디로 갈까 → search_places. 장소 이름이 훈련이나 산책 질문의 배경으로 나온 것뿐이면
  장소 요청이 아닙니다.
- 위 어느 것도 답하지 않는 일반 돌봄·사육·습성·건강 걱정(급여, 음수, 목욕, 수면, 준비물,
  "이 정도면 괜찮은가") → answer_generally. 같은 발화에 전문 도구가 맞는 부분이 있으면 그 도구에
  **더해** 부르고 대신하지 않으며, 반려견과 무관한 요청(사람 음식·금융·사람용 날씨 등)은 어느
  도구도 부르지 않습니다. 발화에 **별도의** 돌봄 질문이 있을 때만이고, 훈련·제도·산책·장소로
  온전히 답해지는 질문에는 부르지 않습니다 — 개·견종·나이·증상이 배경으로 언급된 것만으로는
  일반 답변이 아니고, 행동 질문에서 훈련과 일반 사이가 애매하면 훈련만 부릅니다.
- 눈에 보이는 피부 상태 → hand_off_to_skin
- 걸음걸이·절뚝임·자세 → hand_off_to_gait
- 발화 전체가 인사·감사·작별뿐 → reply_socially

필요한 도구를 다 불렀으면 짧게 마무리만 하세요. 사용자에게 나가는 문장은 시스템이
자동으로 만듭니다."""


def build_agent_model(*, callbacks: list[Any] | None = None) -> ChatGoogleGenerativeAI:
    """에이전트의 채팅 모델. 런타임과 비교 러너가 **같은 함수**로 만든다 (#272).

    설정값은 의미 라우터의 상수를 그대로 쓴다 — temperature · 후보 수 · 출력 상한 ·
    타임아웃이 같아야 두 구현의 차이가 "고르는 방식" 에서만 온다. `callbacks` 는 러너가
    턴별 사용량을 재는 자리다 (`with_config()` 로 감싸면 `RunnableBinding` 이 되어
    `create_agent` 의 `bind_tools` 경로가 흔들린다).
    """
    return ChatGoogleGenerativeAI(
        model=AGENT_MODEL_ID,
        google_api_key=settings.gemini_api_key.get_secret_value(),
        temperature=ROUTER_TEMPERATURE,
        n=ROUTER_CANDIDATE_COUNT,
        max_output_tokens=ROUTER_MAX_OUTPUT_TOKENS,
        max_retries=AGENT_MAX_RETRIES,
        timeout=settings.gemini_timeout_ms / 1_000,
        callbacks=callbacks,
    )


def build_agent_user_message(*, query: str, context: dict[str, Any]) -> str:
    """모델이 보는 입력 표면. 의미 라우터의 프롬프트 꼬리와 같은 키·같은 검증이다.

    v1 에이전트는 질문만 봤다. 라우터가 보는 라우팅 메타데이터(source · action ·
    active_dog_id)를 에이전트만 못 보면 그 케이스에서 두 구현은 다른 입력으로 잰 것이
    된다. 좌표는 여전히 어느 쪽도 안 본다 (D-051).
    """
    if not query.strip():
        raise ValueError("query must not be blank")
    metadata = routing_metadata(context)
    return (
        f"ROUTING_METADATA: {json.dumps(metadata, ensure_ascii=False, sort_keys=True)}\n"
        f"USER_QUERY: {query}"
    )


class AgentOrchestrationService:
    """`Orchestrator` Protocol 구현. `runtime.build_orchestrator("agent")` 가 만든다."""

    def __init__(
        self,
        *,
        model: Any = None,
        engine: OrchestrationEngine | None = None,
        toolbox_factory: Any = None,
    ) -> None:
        """`model` · `engine` · `toolbox_factory` 는 테스트와 러너가 갈아끼우는 자리다.

        모델 기본값을 여기서 만들지 않고 `run` 에서 미루는 이유: 생성만으로 API 키를
        요구하면 키 없는 개발 PC 에서 `build_orchestrator("agent")` 조차 못 부른다. 키가
        없는 것은 **그 요청의 실패**이지 프로세스의 실패가 아니다 — 의미 라우터가 같은
        판단이다. 엔진은 LangGraph 서비스와 같은 기본값이다.
        """
        self._model = model
        self._engine = engine or OrchestrationEngine()
        self._toolbox_factory = toolbox_factory or CapabilityToolbox

    async def run(
        self,
        *,
        query: str,
        principal: PrincipalContext,
        context: dict[str, Any] | None = None,
        requested_capability: str | None = None,
        request_id: str | None = None,
        locale: str = "ko-KR",
        include_route_trace: bool = False,
    ) -> AssistantResponse:
        if locale != "ko-KR":
            raise ValueError("v1 supports locale ko-KR only")
        rid = request_id or str(uuid.uuid4())
        structured_context = dict(context or {})
        # 엔진도 거르지만, 모델이 엔진보다 먼저 돈다 — 날 자격증명이 프롬프트에 닿기 전에.
        _reject_raw_credentials(structured_context)

        # 요청의 루트 런 (`orchestration/service.py` 와 같은 자리 · 같은 이유). 선택
        # 루프와 엔진이 전부 이 아래 자식이다. 전에는 선택 루프와 엔진이 **둘 다**
        # `run_id=request_id` 인 루트를 만들어 같은 id 의 런이 둘이었다.
        root = self._trace_config(request_id=rid, principal=principal)
        async with request_trace(
            request_id=rid,
            run_name=root["run_name"],
            inputs={
                "query": query,
                "requested_capability": requested_capability,
                "context": structured_context,
                "locale": locale,
            },
            metadata=root["metadata"],
            tags=root["tags"],
        ):
            return await self._plan_and_execute(
                query=query,
                principal=principal,
                structured_context=structured_context,
                requested_capability=requested_capability,
                rid=rid,
                locale=locale,
                include_route_trace=include_route_trace,
            )

    async def _plan_and_execute(
        self,
        *,
        query: str,
        principal: PrincipalContext,
        structured_context: dict[str, Any],
        requested_capability: str | None,
        rid: str,
        locale: str,
        include_route_trace: bool,
    ) -> AssistantResponse:
        # 응급은 라우터보다 앞이다 — LangGraph 쪽(`../service.py`)과 같은 자리·같은 이유:
        # 모델을 태우지 않고, 배타로 끝낸다. `_is_night` 는 그 모듈 것을 그대로 쓴다 — 야간
        # 경계를 두 곳에 따로 두면 시간이 지나며 갈라진다.
        route_plan = resolve_emergency_route(
            query=query,
            context=structured_context,
            requested_capability=requested_capability,
            at_night=_is_night(datetime.now(tz=_KST)),
        )
        if route_plan is None:
            route_plan = resolve_deterministic_route(
                requested_capability=requested_capability,
                query=query,
                context=structured_context,
            )
        if route_plan is None:
            # 라우터와 같은 자리에서 같은 예외로 — 잘못된 라우팅 메타데이터는 어느
            # 구현에서도 모델에 닿지 않는다.
            content = build_agent_user_message(query=query, context=structured_context)
            try:
                decision = await self._select(content, request_id=rid, principal=principal)
            except Exception:  # noqa: BLE001 - 계획 동결 전의 실패는 전부 O-14 다
                # CLARIFY 가 아니다. 모델의 잘못된 출력은 사용자에게 안 보인다. 그리고
                # 어댑터는 아직 하나도 안 돌았으므로 잃을 결과도 없다 — 부분 선택을
                # 계획으로 승격하지 않는다 (모듈 docstring).
                return AssistantResponse(
                    request_id=rid,
                    status=AssistantStatus.FAILED,
                    message=_ROUTER_FAILURE_MESSAGE,
                    results=[],
                    handoffs=[],
                    clarify=None,
                    route=self._trace(include_route_trace),
                )
            if decision.social_intent is not None:
                # LangGraph 쪽과 같은 고정 문구. 모델이 지은 인사말을 내보내면 스몰토크
                # 문구가 두 구현에서 달라지고, 그건 비교하려는 축이 아니다.
                return build_social_response(
                    request_id=rid,
                    intent=decision.social_intent,
                    route=self._trace(include_route_trace),
                )
            route_plan = assemble_route_plan(
                decision,
                query=query,
                context=structured_context,
                router=RouterKind.LLM,
                model=AGENT_MODEL_ID,
                prompt_version=AGENT_PROMPT_VERSION,
                # LangGraph 쪽과 **같은 값 · 같은 규칙**이다 (#279). 툴을 하나도 안 부르고
                # 마친 것이 라우터의 빈 결정과 같은 길로 폴백을 지난다.
                general_fallback=settings.general_fallback,
            )

        return await self._engine.run(
            route_plan=route_plan,
            query=query,
            principal=principal,
            request_id=rid,
            locale=locale,
            context=structured_context,
            include_route_trace=include_route_trace,
        )

    async def _select(
        self, content: str, *, request_id: str, principal: PrincipalContext
    ) -> SemanticRoutingDecision:
        """에이전트 루프를 한 번 돌려 **선택만** 가져온다. 최종 텍스트는 버린다.

        답 문장은 `aggregate_results` 가 도구 결과로 만든다. 모델의 마지막 말을 쓰면
        두 구현의 문장 품질이 섞여 들어와 "무엇을 골랐나"를 못 잰다. 모델이 여기서
        하는 일은 오직 도구 선택이다.
        """
        toolbox = self._toolbox_factory()
        agent = create_agent(
            self._model or self._default_model(),
            toolbox.as_tools(),
            system_prompt=_SYSTEM_PROMPT,
        )
        await asyncio.wait_for(
            agent.ainvoke(
                {"messages": [{"role": "user", "content": content}]},
                config={
                    # 루트(`assistant_query_agent`, `run()`)의 자식이다. `run_id` 를
                    # 여기서도 `request_id` 로 주면 루트와 같은 id 가 둘이 된다.
                    **trace_config(request_id=request_id, run_name="agent_select", root=False),
                    # 루프 상한. 답이 아니라 **안전장치**다 — 에이전트가 루프를 돌아
                    # 비싼 것 자체는 비교가 재야 할 발견이므로 여기서 깎지 않는다.
                    "recursion_limit": settings.agent_recursion_limit,
                },
            ),
            timeout=settings.agent_turn_timeout_ms / 1_000,
        )
        return toolbox.decision()

    @staticmethod
    def _trace_config(*, request_id: str, principal: PrincipalContext) -> dict[str, Any]:
        """`graph.py` 와 **같은 metadata 키**로 트레이스를 남긴다 (D-054).

        키가 갈리면 두 구현의 트레이스를 같은 쿼리로 못 거르고, 그러면 비교가 재려는
        지연·토큰 비용이 한쪽에서만 나온다.

        **`run_name` 만 다르다.** 두 구현을 트레이스에서 갈라 보는 자리가 필요하고,
        `graph.py` 를 건드리지 않고 그것을 얻는 가장 싼 방법이다. 이 config 는 `run()` 의
        **루트 런**(`request_trace`)에 붙는다 — 선택 루프(`agent_select`)와 엔진
        (`orchestration_engine`)은 그 아래 자식으로, 각자 `run_id` 없이 남긴다.

        **`tags` 는 비운다.** `graph.py` 는 RoutePlan 이 이미 있어 `cap:training` 을 미리
        달 수 있지만, 에이전트는 무엇을 부를지 돌기 전에 모른다.
        """
        return trace_config(
            request_id=request_id,
            run_name="assistant_query_agent",
            metadata={
                "principal_kind": principal.kind,
                "router": RouterKind.LLM.value,
                "router_model": AGENT_MODEL_ID,
                "prompt_version": AGENT_PROMPT_VERSION,
                "locale": "ko-KR",
            },
        )

    def _default_model(self) -> Any:
        return build_agent_model()

    @staticmethod
    def _trace(include_route_trace: bool) -> RouteTrace | None:
        if not include_route_trace:
            return None
        return RouteTrace(
            router=RouterKind.LLM, model=AGENT_MODEL_ID, prompt_version=AGENT_PROMPT_VERSION
        )


__all__ = [
    "AGENT_MAX_RETRIES",
    "AGENT_MODEL_ID",
    "AGENT_PROMPT_VERSION",
    "AgentOrchestrationService",
    "build_agent_model",
    "build_agent_user_message",
]
