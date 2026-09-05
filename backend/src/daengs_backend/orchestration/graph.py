"""Minimal LangGraph that executes an already-constructed RoutePlan."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from typing import Protocol, cast

from langgraph.graph import END, START, StateGraph

from daengs_backend.core.tracing import trace_config
from daengs_backend.orchestration.adapters import (
    LifeCapabilityAdapter,
    PlaceCapabilityAdapter,
    TrainingCapabilityAdapter,
    WalkCapabilityAdapter,
)
from daengs_backend.orchestration.aggregate import aggregate_results
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    ErrorDetail,
    OrchestratorState,
    PrincipalContext,
    RoutePlan,
)

_FORBIDDEN_CONTEXT_KEYS = frozenset(
    {"authorization", "jwt", "jwe", "access_token", "refresh_token", "cookie", "cookie_token"}
)


class CapabilityAdapter(Protocol):
    capability: CapabilityName

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult: ...


class OrchestrationEngine:
    """Sequential v1 execution; no router, persistence, checkpointer, or subgraphs."""

    def __init__(self, adapters: Mapping[CapabilityName, CapabilityAdapter] | None = None) -> None:
        if adapters is None:
            adapters = {
                CapabilityName.TRAINING: TrainingCapabilityAdapter(),
                CapabilityName.LIFE: LifeCapabilityAdapter(),
                CapabilityName.WALK: WalkCapabilityAdapter(),
                CapabilityName.PLACE: PlaceCapabilityAdapter(),
            }
        self._adapters = dict(adapters)
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(OrchestratorState)
        builder.add_node("validate_route_plan", self._validate_route_plan)
        builder.add_node("execute_requests", self._execute_requests)
        builder.add_node("aggregate", self._aggregate)
        builder.add_edge(START, "validate_route_plan")
        builder.add_conditional_edges(
            "validate_route_plan",
            self._after_validation,
            {"execute": "execute_requests", "aggregate": "aggregate"},
        )
        builder.add_edge("execute_requests", "aggregate")
        builder.add_edge("aggregate", END)
        return builder.compile()

    async def run(
        self,
        *,
        route_plan: RoutePlan,
        query: str,
        principal: PrincipalContext,
        request_id: str | None = None,
        locale: str = "ko-KR",
        context: dict | None = None,
        include_route_trace: bool = False,
    ) -> AssistantResponse:
        if locale != "ko-KR":
            raise ValueError("v1 supports locale ko-KR only")
        structured_context = dict(context or {})
        _reject_raw_credentials(structured_context)
        rid = request_id or str(uuid.uuid4())
        initial: OrchestratorState = {
            "request_id": rid,
            "principal": principal,
            "query": query,
            "locale": "ko-KR",
            "context": structured_context,
            "route_plan": route_plan,
            "results": [],
            "response": None,
            "include_route_trace": include_route_trace,
        }
        # 트레이싱이 꺼져 있으면 이 config 는 그냥 안 읽힙니다 (`core.tracing.trace_config`).
        # metadata 에는 **D-037 이 기본 관측에 허용한 것만** 담습니다 — 질문 원문은
        # 여기가 아니라 노드 input 으로 갑니다. 그쪽은 anonymizer 를 거치지만
        # metadata 는 우리가 무엇을 넣었는지가 곧 계약이라, 이 목록을 늘릴 때는
        # 그 값이 "라우팅 종류·상태·코드" 급인지 먼저 물어야 합니다.
        final = await self.graph.ainvoke(
            initial,
            config=trace_config(
                request_id=rid,
                run_name="assistant_query",
                metadata={
                    "principal_kind": principal.kind,
                    "router": route_plan.router.value,
                    "router_model": route_plan.model,
                    "prompt_version": route_plan.prompt_version,
                    "locale": "ko-KR",
                },
                # 능력별로 트레이스를 거를 수 있게 태그로 답니다. "훈련 답변이 이상하다"
                # 는 민원을 `cap:training` 으로 좁히는 것이 첫 동작이라서입니다.
                tags=[f"cap:{request.capability.value}" for request in route_plan.requests],
            ),
        )
        response = final.get("response")
        if response is None:
            raise RuntimeError("orchestration graph completed without a response")
        return cast(AssistantResponse, response)

    @staticmethod
    async def _validate_route_plan(state: OrchestratorState) -> dict:
        plan = state["route_plan"]
        if plan.clarify is not None and (plan.requests or plan.handoffs):
            raise ValueError("clarify is exclusive with requests and handoffs")
        return {}

    @staticmethod
    def _after_validation(state: OrchestratorState) -> str:
        plan = state["route_plan"]
        return "execute" if plan.clarify is None and plan.requests else "aggregate"

    async def _execute_requests(self, state: OrchestratorState) -> dict:
        results: list[CapabilityResult] = []
        for request in state["route_plan"].requests:
            adapter = self._adapters.get(request.capability)
            if adapter is None:
                results.append(
                    CapabilityResult(
                        capability=request.capability,
                        status=CapabilityStatus.ERROR,
                        error=ErrorDetail(
                            kind="unsupported_capability",
                            detail=f"지원하지 않는 기능입니다: {request.capability.value}",
                        ),
                        elapsed_ms=0,
                    )
                )
                continue
            started = time.perf_counter()
            try:
                pending = adapter.run(request, request_id=state["request_id"])
                if request.timeout_ms is None:
                    result = await pending
                else:
                    # This is a response deadline, not hard cancellation: blocking
                    # asyncio.to_thread() work may continue. Domain/provider timeouts
                    # remain the execution bound, so retry policy must allow for a
                    # timed-out invocation that is still completing.
                    result = await asyncio.wait_for(pending, timeout=request.timeout_ms / 1_000)
            except TimeoutError:
                result = CapabilityResult(
                    capability=request.capability,
                    status=CapabilityStatus.TIMEOUT,
                    error=ErrorDetail(
                        kind="orchestration_timeout",
                        detail="기능 실행 시간이 초과됐습니다.",
                    ),
                    elapsed_ms=int((time.perf_counter() - started) * 1_000),
                )
            except Exception as exc:  # noqa: BLE001 - contain one adapter's unexpected failure
                result = CapabilityResult(
                    capability=request.capability,
                    status=CapabilityStatus.ERROR,
                    error=ErrorDetail(
                        kind=type(exc).__name__,
                        detail="기능 실행 중 예기치 않은 오류가 발생했습니다.",
                    ),
                    elapsed_ms=int((time.perf_counter() - started) * 1_000),
                )
            results.append(result)
        return {"results": results}

    @staticmethod
    async def _aggregate(state: OrchestratorState) -> dict:
        response = aggregate_results(
            request_id=state["request_id"],
            route_plan=state["route_plan"],
            results=state["results"],
            include_route_trace=state["include_route_trace"],
        )
        return {"response": response}


def _reject_raw_credentials(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower().replace("-", "_") in _FORBIDDEN_CONTEXT_KEYS:
                raise ValueError("raw authentication credentials are forbidden in graph context")
            _reject_raw_credentials(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_raw_credentials(item)


__all__ = ["CapabilityAdapter", "OrchestrationEngine"]
