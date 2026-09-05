"""LangChain tool-calling 에이전트로 `/assistant/query` 를 답한다.

LangGraph 쪽(`../service.py`)은 **먼저 RoutePlan 을 짜고** 그대로 실행한다. 여기서는
모델이 능력을 툴로 쥐고 스스로 고르며 루프를 돈다. 그것이 유일한 차이여야 한다 —
나머지는 전부 공유한다:

- 입력: `planner._payload_for` 가 신뢰된 query·context 에서만 payload 를 만든다.
- 실행: 같은 `adapters/`.
- 집계: 같은 `aggregate_results`. 에이전트가 문장을 스스로 지어도 **상태 판정은
  진리표를 지난다** — `AssistantResponse` 8상태가 갈리면 카드 ③이 잴 것이 없다.

**되돌려 만든 RoutePlan.** 에이전트는 계획을 만들지 않지만, 다 돌고 나서 "실제로 무엇을
불렀나"를 RoutePlan 모양으로 합친다. 이유가 둘이다. ⑴ `aggregate_results` 가 RoutePlan
을 받는다. ⑵ 골드 세트(`evals/orchestration_router/`)가 RoutePlan 을 채점하므로, 이
모양이라야 두 구현을 같은 자로 잴 수 있다. 순서·타임아웃 같은 계획 세부는 못 재고
"무엇을 불렀나"만 잰다 — 그게 두 패러다임이 공유하는 유일한 축이다.

**모델은 자기 지식으로 답하지 않는다.** 툴 결과만이 답의 재료다. 이것을 안 박아 두면
에이전트는 v1 이 일부러 라우팅하지 않기로 한 일반 육아 질문까지 답해 버리고, 그러면
비교가 "오케스트레이션 전략"이 아니라 "안전 정책"의 비교가 된다 (semantic.py v5·v6).

**모델을 Gemini 로 고정한 것도 비교 설계다.** 의미 라우터와 같은 계열이라야 카드 ③의
결과에서 "에이전트라서 좋아진 것"과 "모델이 달라서 좋아진 것"이 섞이지 않는다.
"""

from __future__ import annotations

import asyncio
import uuid
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
from daengs_backend.orchestration.agent.tools import CapabilityToolbox
from daengs_backend.orchestration.aggregate import aggregate_results
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    PrincipalContext,
    RoutePlan,
    RouterKind,
    RouteTrace,
)

# `graph.py` 의 자격증명 차단과 `service.py` 의 실패 문구를 그대로 쓴다. 둘 다 두
# 구현이 **같아야** 하는 것이다 — 보호가 한쪽에만 있으면 실험 구현이 구멍이 되고,
# 실패 문구가 갈리면 사용자에게 실패가 두 종류로 보인다. 사본을 만들지 않는다.
from daengs_backend.orchestration.graph import _reject_raw_credentials
from daengs_backend.orchestration.planner import resolve_deterministic_route
from daengs_backend.orchestration.service import _ROUTER_FAILURE_MESSAGE
from daengs_backend.orchestration.social import build_social_response

AGENT_MODEL_ID = "gemini-3.1-flash-lite"
AGENT_PROMPT_VERSION = "agent-ko-v1"

_SYSTEM_PROMPT = """당신은 DAENGS 반려견 비서입니다. 한국어로 답합니다.

**당신은 스스로 답하지 않습니다.** 답의 재료는 도구가 돌려준 것뿐입니다. 도구가 주지
않은 사실을 지어내거나, 당신이 알고 있는 일반 상식으로 채우지 마세요. 진단하지 않습니다.

무엇을 물었는지 보고 필요한 도구를 **모두** 부르세요. 한 발화가 두 가지를 물으면 둘 다
부릅니다. 필요 없는 도구는 부르지 않습니다.

경계:
- 행동을 바꾸거나 가르치는 것 → ask_training
- 제도·법령·행정·정책·계약의 공식 정보 → ask_life. **일반적인 사육·돌봄 조언은 여기가
  아니고, 다른 어떤 도구도 아닙니다.** 산책 횟수, 급여량, 수면, 음수량, 견종·나이별
  돌봄 같은 통상적 조언은 이 서비스가 답하지 않습니다. 그럴 때는 도구를 부르지 말고
  답할 수 없다고만 하세요.
- 지금 나가도 되는 환경인가 → check_walk_conditions
- 어디로 갈까 → search_places. 장소 이름이 훈련이나 산책 질문의 배경으로 나온 것뿐이면
  장소 요청이 아닙니다.
- 눈에 보이는 피부 상태 → hand_off_to_skin
- 걸음걸이·절뚝임·자세 → hand_off_to_gait
- 발화 전체가 인사·감사·작별뿐 → reply_socially

도구를 부른 뒤에는 짧게 마무리만 하세요. 사용자에게 나가는 문장은 도구 결과로부터
자동으로 만들어지므로, 결과를 길게 옮겨 적을 필요가 없습니다."""


class AgentOrchestrationService:
    """`Orchestrator` Protocol 구현. `runtime.build_orchestrator("agent")` 가 만든다."""

    def __init__(self, *, model: Any = None, toolbox_factory: Any = None) -> None:
        """`model` 과 `toolbox_factory` 는 테스트가 갈아끼우는 자리다.

        기본값을 여기서 만들지 않고 `run` 에서 미루는 이유: 생성만으로 API 키를 요구하면
        키 없는 개발 PC 에서 `build_orchestrator("agent")` 조차 못 부른다. 키가 없는 것은
        **그 요청의 실패**이지 프로세스의 실패가 아니다 — 의미 라우터가 같은 판단이다.
        """
        self._model = model
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
        _reject_raw_credentials(structured_context)
        toolbox = self._toolbox_factory(
            query=query, context=structured_context, request_id=rid
        )

        deterministic = resolve_deterministic_route(
            requested_capability=requested_capability,
            query=query,
            context=structured_context,
        )
        if deterministic is not None:
            # **명시 신호는 두 구현에서 똑같이 동작해야 한다** (D-036: 라우팅 신호이지
            # 인가가 아니다). 여기서 에이전트를 부르면 "무엇을 부를지"를 이미 정해 준
            # 요청에까지 모델 비용이 붙고, 비교는 의미 경로에서만 뜻이 있다.
            for request in deterministic.requests:
                await toolbox.run_request(request)
            return aggregate_results(
                request_id=rid,
                route_plan=deterministic,
                results=toolbox.results,
                include_route_trace=include_route_trace,
            )

        try:
            await self._invoke_agent(query, toolbox)
        except Exception:  # noqa: BLE001 - 모델/시스템 실패는 O-14 로 FAILED 다
            # CLARIFY 가 아니다. 그리고 모델의 잘못된 출력은 사용자에게 안 보인다.
            #
            # **이미 나온 답은 버리지 않는다.** 의미 라우터는 실행 *전에* 실패하므로
            # 잃을 것이 없지만, 에이전트는 루프 중간에 죽는다 — 훈련 답을 받아 놓고
            # 다음 턴에 프로바이더가 끊긴 요청까지 FAILED 로 돌려보내면, 멀쩡히 돈
            # 능력의 결과를 우리가 지우는 것이 된다. 타임아웃도 같다.
            # ⑤ 의 CLARIFY 와 같은 계열의 갈림이고, 카드 ③은 알고 채점해야 한다.
            if not toolbox.results:
                return AssistantResponse(
                    request_id=rid,
                    status=AssistantStatus.FAILED,
                    message=_ROUTER_FAILURE_MESSAGE,
                    results=[],
                    handoffs=[],
                    clarify=None,
                    route=self._trace(include_route_trace),
                )

        if toolbox.social_intent is not None and not toolbox.requests and not toolbox.handoffs:
            # LangGraph 쪽과 같은 고정 문구를 쓴다. 여기서 모델이 지은 인사말을 내보내면
            # 스몰토크 문구가 두 구현에서 달라지고, 그건 비교하려는 축이 아니다.
            return build_social_response(
                request_id=rid,
                intent=toolbox.social_intent,
                route=self._trace(include_route_trace),
            )

        return aggregate_results(
            request_id=rid,
            route_plan=self._route_plan(toolbox),
            results=toolbox.results,
            include_route_trace=include_route_trace,
        )

    async def _invoke_agent(self, query: str, toolbox: CapabilityToolbox) -> None:
        """에이전트를 한 턴 돌린다. 최종 텍스트는 **버린다.**

        답 문장은 `aggregate_results` 가 도구 결과로 만든다. 모델의 마지막 말을 쓰면
        두 구현의 문장 품질이 섞여 들어와 "무엇을 골랐나"를 못 잰다. 모델이 여기서
        하는 일은 오직 도구 선택이다.
        """
        agent = create_agent(
            self._model or self._default_model(),
            toolbox.as_tools(),
            system_prompt=_SYSTEM_PROMPT,
        )
        await asyncio.wait_for(
            agent.ainvoke(
                {"messages": [{"role": "user", "content": query}]},
                # 루프 상한. 답이 아니라 **안전장치**다 — 에이전트가 루프를 돌아 비싼
                # 것 자체는 카드 ③이 재야 할 발견이므로 여기서 깎지 않는다.
                config={"recursion_limit": settings.agent_recursion_limit},
            ),
            timeout=settings.agent_turn_timeout_ms / 1_000,
        )

    def _default_model(self) -> Any:
        return ChatGoogleGenerativeAI(
            model=AGENT_MODEL_ID,
            google_api_key=settings.gemini_api_key.get_secret_value(),
            timeout=settings.gemini_timeout_ms / 1_000,
        )

    @staticmethod
    def _route_plan(toolbox: CapabilityToolbox) -> RoutePlan:
        """실행 기록을 RoutePlan 으로 되돌려 만든다 (모듈 docstring).

        CLARIFY 는 배타다 (O-8). 좌표가 없어 막혔는데 **아무 능력도 답을 못 냈으면**
        위치를 묻는다. 하나라도 답이 나왔으면 그 답이 이긴다 — planner 는 선택 전체를
        실행 전에 게이트할 수 있지만 에이전트는 하나씩 부르며 알게 되므로, 이미 나온
        답을 버리고 되물을 수는 없다. **두 구현이 갈리는 유일한 계약 지점이고, 카드 ③은
        이것을 알고 채점해야 한다.**
        """
        if toolbox.clarify is not None and not toolbox.results:
            return RoutePlan(
                requests=[],
                handoffs=[],
                clarify=toolbox.clarify,
                router=RouterKind.LLM,
                model=AGENT_MODEL_ID,
                prompt_version=AGENT_PROMPT_VERSION,
            )
        return RoutePlan(
            requests=list(toolbox.requests),
            handoffs=list(toolbox.handoffs),
            clarify=None,
            router=RouterKind.LLM,
            model=AGENT_MODEL_ID,
            prompt_version=AGENT_PROMPT_VERSION,
        )

    @staticmethod
    def _trace(include_route_trace: bool) -> RouteTrace | None:
        if not include_route_trace:
            return None
        return RouteTrace(
            router=RouterKind.LLM, model=AGENT_MODEL_ID, prompt_version=AGENT_PROMPT_VERSION
        )


__all__ = ["AGENT_MODEL_ID", "AGENT_PROMPT_VERSION", "AgentOrchestrationService"]
