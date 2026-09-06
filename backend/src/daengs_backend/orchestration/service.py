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

import uuid
from typing import Any

from daengs_backend.config import settings
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    PrincipalContext,
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
        semantic_trace = (
            RouteTrace(
                router=RouterKind.LLM, model=ROUTER_MODEL_ID, prompt_version=PROMPT_VERSION
            )
            if include_route_trace
            else None
        )
        structured_context = dict(context or {})
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
            except SemanticRoutingError:
                return AssistantResponse(
                    request_id=rid,
                    status=AssistantStatus.FAILED,
                    message=_ROUTER_FAILURE_MESSAGE,
                    results=[],
                    handoffs=[],
                    clarify=None,
                    route=semantic_trace,
                )
            if decision.social_intent is not None:
                # Schema guarantees execute/handoffs are empty here: nothing to plan or run.
                return build_social_response(
                    request_id=rid, intent=decision.social_intent, route=semantic_trace
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
        return await self._engine.run(
            route_plan=route_plan,
            query=query,
            principal=principal,
            request_id=rid,
            locale=locale,
            context=structured_context,
            include_route_trace=include_route_trace,
        )


__all__ = ["AssistantOrchestrationService"]
