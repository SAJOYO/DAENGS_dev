"""Thin planning layer in front of the existing Card 1 execution core.

Deterministic signal first, semantic Gemini selection only as the fallback, then
the plan goes to the untouched OrchestrationEngine. A router/system failure
(O-14) executes nothing and returns top-level FAILED — it is never CLARIFY, and
the invalid model output is never surfaced. A purely social utterance
(greeting/thanks/goodbye, classified by the router with no capability intent) is
answered by a fixed template before any RoutePlan exists, so it never reaches
the engine. `/assistant/query` itself is Card 3; this module deliberately
registers no endpoint.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from daengs_backend.config import settings
from daengs_backend.core.tracing import request_trace
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    PrincipalContext,
    RoutePlan,
    RouterKind,
    RouteTrace,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.planner import assemble_route_plan, resolve_deterministic_route
from daengs_backend.orchestration.semantic import (
    PROMPT_VERSION,
    ROUTER_MODEL_ID,
    GeminiSemanticRouter,
    SemanticRoutingError,
)
from daengs_backend.orchestration.social import build_social_response

_ROUTER_FAILURE_MESSAGE = "요청을 해석하지 못했습니다. 잠시 후 다시 시도해 주세요."
LOGGER = logging.getLogger(__name__)


class AssistantOrchestrationService:
    def __init__(
        self,
        *,
        engine: OrchestrationEngine | None = None,
        semantic_router: GeminiSemanticRouter | None = None,
    ) -> None:
        self._engine = engine or OrchestrationEngine()
        self._semantic_router = semantic_router or GeminiSemanticRouter()

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
        """Plan, then execute. `include_route_trace` is the caller's answer to "may this
        principal see how the request was routed?" (#238) — the HTTP boundary decides it,
        because permissions are its business, and it defaults to no.

        The two responses that never reach the engine get the same trace attached here:
        a social reply and a router failure are exactly the answers whose "no capability
        ran" is otherwise unexplained in the console.
        """
        rid = request_id or str(uuid.uuid4())
        structured_context = dict(context or {})
        # 요청의 루트 런. 시맨틱 라우터의 LLM 런과 그래프(자식 `orchestration_engine`)가
        # 전부 이 아래에 붙는다 — 라우터가 그래프보다 먼저 돌아서, 루트를 그래프에
        # 두면 라우터 런이 어느 트레이스에도 못 붙는다 (`core.tracing` 모듈 docstring).
        # 트레이싱이 꺼져 있으면 이 컨텍스트는 아무것도 안 보낸다.
        async with request_trace(
            request_id=rid,
            run_name="assistant_query",
            inputs={
                "query": query,
                "requested_capability": requested_capability,
                "context": structured_context,
                "locale": locale,
            },
            metadata={"principal_kind": principal.kind, "locale": locale},
        ) as run:
            response, route_plan = await self._plan_and_execute(
                query=query,
                principal=principal,
                structured_context=structured_context,
                requested_capability=requested_capability,
                rid=rid,
                locale=locale,
                include_route_trace=include_route_trace,
            )
            # 라우팅 종류는 돌고 나서야 안다. 자식(그래프)의 metadata 와 같은 키다 —
            # 두 구현(`agent/service.py`)의 루트를 같은 쿼리로 거르는 계약.
            run.add_metadata(_route_metadata(route_plan))
            run.end(outputs=_trace_outputs(response))
            return response

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
    ) -> tuple[AssistantResponse, RoutePlan | None]:
        """계획하고 실행한다. 둘째 반환값은 트레이스 metadata 용 — 엔진에 못 간 두 응답
        (스몰토크 · 라우터 실패)은 RoutePlan 이 없어서 None 이다."""
        semantic_trace = (
            RouteTrace(
                router=RouterKind.LLM, model=ROUTER_MODEL_ID, prompt_version=PROMPT_VERSION
            )
            if include_route_trace
            else None
        )
        route_plan = resolve_deterministic_route(
            requested_capability=requested_capability,
            query=query,
            context=structured_context,
        )
        if route_plan is None:
            try:
                decision = await self._semantic_router.select(
                    query=query, context=structured_context
                )
            except SemanticRoutingError as exc:
                # 사용자에게는 고정 문구만 나가고 모델의 잘못된 출력은 안 보인다 (O-14).
                # 그래서 **여기가 원인이 남는 유일한 자리**다 — 프로바이더 예외(한도 초과 ·
                # 타임아웃)와 스키마 실패가 같은 FAILED 로 나가는데, 로그가 없으면 둘을
                # 못 가른다 (2026-09-07 개발 PC 에서 그 상태로 두 번 헛돌았다). 질문 원문은
                # 안 남긴다 (D-037) — request_id 로 트레이스와 잇는다.
                LOGGER.warning(
                    "시맨틱 라우터 실패 request_id=%s: %s (원인: %r)",
                    rid,
                    exc,
                    exc.__cause__,
                )
                return (
                    AssistantResponse(
                        request_id=rid,
                        status=AssistantStatus.FAILED,
                        message=_ROUTER_FAILURE_MESSAGE,
                        results=[],
                        handoffs=[],
                        clarify=None,
                        route=semantic_trace,
                    ),
                    None,
                )
            if decision.social_intent is not None:
                # Schema guarantees execute/handoffs are empty here: nothing to plan or run.
                return (
                    build_social_response(
                        request_id=rid, intent=decision.social_intent, route=semantic_trace
                    ),
                    None,
                )
            route_plan = assemble_route_plan(
                decision,
                query=query,
                context=structured_context,
                router=RouterKind.LLM,
                model=ROUTER_MODEL_ID,
                # 읽는 자리가 여기(요청 시점)인 것은 의도다 — 모듈 최상단에서 읽으면 테스트가
                # 플래그를 켜고 끌 수 없고, 서버는 `.env` 한 줄로 켜고 재시작한다 (#279).
                general_fallback=settings.general_fallback,
            )
        response = await self._engine.run(
            route_plan=route_plan,
            query=query,
            principal=principal,
            request_id=rid,
            locale=locale,
            context=structured_context,
            include_route_trace=include_route_trace,
        )
        return response, route_plan


def _route_metadata(route_plan: RoutePlan | None) -> dict[str, Any]:
    """루트 런 metadata 의 라우팅 키. `graph.py` 의 자식 런과 **같은 키**를 쓴다.

    RoutePlan 이 없는 두 응답(스몰토크 · 라우터 실패)은 시맨틱 라우터를 거친 뒤라
    LLM 라우팅으로 적는다 — 결정론 라우팅은 RoutePlan 없이 끝나는 길이 없다.
    """
    if route_plan is None:
        return {
            "router": RouterKind.LLM.value,
            "router_model": ROUTER_MODEL_ID,
            "prompt_version": PROMPT_VERSION,
        }
    return {
        "router": route_plan.router.value,
        "router_model": route_plan.model,
        "prompt_version": route_plan.prompt_version,
    }


def _trace_outputs(response: AssistantResponse) -> dict[str, Any]:
    """루트 런 출력. **상태만** 싣는다 — 목록에서 실패·명확화를 바로 거르기 위한 것.

    답 본문·근거 청크는 자식 런(그래프 최종 상태 · `training_rag`)에 이미 있어서
    여기 또 실으면 같은 텍스트가 한 요청에 두 번 나간다.

    맨 앞의 가드 이유는 `daengs_training/service.py` `_trace_outputs` 와 같다.
    """
    from langsmith import utils as ls_utils

    if not ls_utils.tracing_is_enabled():
        return {}
    return {
        "status": response.status.value,
        "results": [
            {"capability": result.capability.value, "status": result.status.value}
            for result in response.results
        ],
        "handoffs": [handoff.target for handoff in response.handoffs],
        "clarify": response.clarify is not None,
    }


__all__ = ["AssistantOrchestrationService"]
