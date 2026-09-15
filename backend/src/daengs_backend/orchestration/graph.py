"""Minimal LangGraph that executes an already-constructed RoutePlan."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Protocol, cast

from langgraph.graph import END, START, StateGraph

from daengs_backend.core.tracing import trace_config
from daengs_backend.orchestration import planner
from daengs_backend.orchestration.adapters import (
    GeneralCapabilityAdapter,
    LifeCapabilityAdapter,
    PlaceCapabilityAdapter,
    SkinCapabilityAdapter,
    TrainingCapabilityAdapter,
    VetContactCapabilityAdapter,
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
    ScreeningHistory,
)
from daengs_backend.orchestration.execution import JobExecutor
from daengs_backend.orchestration.facility_presentation import present_facility

_FORBIDDEN_CONTEXT_KEYS = frozenset(
    {"authorization", "jwt", "jwe", "access_token", "refresh_token", "cookie", "cookie_token"}
)


class CapabilityAdapter(Protocol):
    capability: CapabilityName

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult: ...


class OrchestrationEngine:
    """Sequential v1 execution; no router, persistence, checkpointer, or subgraphs."""

    def __init__(
        self,
        adapters: Mapping[CapabilityName, CapabilityAdapter] | None = None,
        *,
        place_adapter: CapabilityAdapter | None = None,
        care_log_adapter: CapabilityAdapter | None = None,
    ) -> None:
        if adapters is None:
            adapters = {
                CapabilityName.TRAINING: TrainingCapabilityAdapter(),
                CapabilityName.LIFE: LifeCapabilityAdapter(),
                CapabilityName.WALK: WalkCapabilityAdapter(),
                CapabilityName.PLACE: PlaceCapabilityAdapter(),
                # 플래그(`DAENGS_GENERAL_FALLBACK`)가 꺼져 있으면 planner 가 이 능력을
                # 계획에 넣지 않으므로 등록만 되고 돌지 않는다 (#279).
                CapabilityName.GENERAL: GeneralCapabilityAdapter(),
                # 라우터가 고를 수 없는 능력이다 — `resolve_emergency_route` 만 계획에 넣는다.
                CapabilityName.VET_CONTACT: VetContactCapabilityAdapter(),
                # 라우터가 고를 수 없다 — 판정 기록이 붙은 `skin` 신호만 `resolve_skin_route`
                # 가 계획에 넣는다 (D-079).
                CapabilityName.SKIN: SkinCapabilityAdapter(),
            }
        self._adapters = dict(adapters)
        if place_adapter is not None:
            if place_adapter.capability != CapabilityName.PLACE:
                raise ValueError("the facility override must implement Place")
            self._adapters[CapabilityName.PLACE] = place_adapter
        # 케어 기록 쓰기는 **기본 어댑터가 없다** (위 dict 에 CARE_LOG 가 없는 것이 의도다).
        # 요청마다 만들어 넣어야 하는 이유는 `adapters/care_log.py` 머리말에 있다 —
        # `app_user_id` 를 들고 있고 그것이 "누구 이름으로 기록되는가" 라서다. 안 넣으면
        # `_execute_requests` 가 `unsupported_capability` 로 끝내지만, 실무에서 그 자리에
        # 닿지 않는다: `planner.resolve_care_log_route` 가 `context["care_log_writable"]`
        # 없이는 제안 자체를 안 내므로 승낙받을 제안이 없다. 그 플래그를 세우는 곳과 이
        # 어댑터를 넣는 곳이 `routers/assistant.py` 의 같은 `if` 다.
        if care_log_adapter is not None:
            if care_log_adapter.capability != CapabilityName.CARE_LOG:
                raise ValueError("the care-log override must implement CareLog")
            self._adapters[CapabilityName.CARE_LOG] = care_log_adapter
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
        #
        # **루트가 아니라 자식입니다** (`root=False`). 요청의 루트 런은 서비스가
        # 만들고(`assistant_query` · `assistant_query_agent`), 이 그래프는 그 아래
        # 붙습니다 — 시맨틱 라우터의 LLM 런이 그래프보다 먼저 돌아서, 루트를 여기 두면
        # 그 런이 트레이스 밖에 남습니다. `run_id` 를 여기서도 `request_id` 로 주면 루트와
        # 같은 id 의 런이 둘이 됩니다 (`core.tracing` 모듈 docstring).
        final = await self.graph.ainvoke(
            initial,
            config=trace_config(
                request_id=rid,
                run_name="orchestration_engine",
                root=False,
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
        executor = JobExecutor(concurrency=1)
        for index, request in enumerate(state["route_plan"].requests):
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
            outcome = await executor.run(
                f"{state['request_id']}:{index}:{request.capability.value}",
                lambda adapter=adapter, request=request: adapter.run(
                    request, request_id=state["request_id"]
                ),
                timeout_ms=request.timeout_ms,
            )
            if outcome.status == "ok":
                result = outcome.value
            elif outcome.status == "timeout":
                result = CapabilityResult(
                    capability=request.capability,
                    status=CapabilityStatus.TIMEOUT,
                    error=ErrorDetail(
                        kind="orchestration_timeout",
                        detail="기능 실행 시간이 초과됐습니다.",
                    ),
                    elapsed_ms=outcome.elapsed_ms,
                )
            else:
                result = CapabilityResult(
                    capability=request.capability,
                    status=CapabilityStatus.ERROR,
                    error=ErrorDetail(
                        kind=outcome.error_kind,
                        detail="기능 실행 중 예기치 않은 오류가 발생했습니다.",
                    ),
                    elapsed_ms=outcome.elapsed_ms,
                )
            results.append(result)
        return {"results": results}

    @staticmethod
    async def _aggregate(state: OrchestratorState) -> dict:
        # 이력은 **planner 와 같은 화이트리스트**를 지나서 온다 — 답변에 붙는 절이 payload 와
        # 다른 경로로 컨텍스트를 읽으면 좁힘이 두 벌이 된다 (#79 3번).
        history = planner.screening_history(state["context"])
        plan, results = state["route_plan"], state["results"]
        if state["context"].get("facility_response"):
            plan, results = present_facility(plan, results)
        response = aggregate_results(
            request_id=state["request_id"],
            route_plan=plan,
            results=results,
            include_route_trace=state["include_route_trace"],
            screening_history=ScreeningHistory.model_validate(history) if history else None,
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
