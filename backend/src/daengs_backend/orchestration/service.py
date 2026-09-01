"""Thin planning layer in front of the existing Card 1 execution core.

Deterministic signal first, semantic Gemini selection only as the fallback, then
the plan goes to the untouched OrchestrationEngine. A router/system failure
(O-14) executes nothing and returns top-level FAILED — it is never CLARIFY, and
the invalid model output is never surfaced. `/assistant/query` itself is Card 3;
this module deliberately registers no endpoint.
"""

from __future__ import annotations

import uuid
from typing import Any

from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    PrincipalContext,
    RouterKind,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.planner import assemble_route_plan, resolve_deterministic_route
from daengs_backend.orchestration.semantic import (
    ROUTER_MODEL_ID,
    GeminiSemanticRouter,
    SemanticRoutingError,
)

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
    ) -> AssistantResponse:
        rid = request_id or str(uuid.uuid4())
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
                )
            route_plan = assemble_route_plan(
                decision,
                query=query,
                context=structured_context,
                router=RouterKind.LLM,
                model=ROUTER_MODEL_ID,
            )
        return await self._engine.run(
            route_plan=route_plan,
            query=query,
            principal=principal,
            request_id=rid,
            locale=locale,
            context=structured_context,
        )


__all__ = ["AssistantOrchestrationService"]
