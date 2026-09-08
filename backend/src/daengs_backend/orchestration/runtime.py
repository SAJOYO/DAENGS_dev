"""`/assistant/query` 뒤의 오케스트레이터 구현을 고르는 자리.

LangGraph 는 **정해진 워크플로우**를 돌리는 데 최적화돼 있습니다. 자유도가 필요한
질의에는 LangChain 에이전트 쪽이 나을 수 있는데, 그건 재 봐야 아는 것이라 두 구현을
한 저장소에 병존시키고 골라 씁니다. 이 모듈이 그 "고르는" 한 곳입니다.

**갈아끼울 지점은 여기 하나뿐입니다.** `routers/assistant.py` 는 이미
`run(...) -> AssistantResponse` 만 부르고 그 안을 모르므로, 구현을 바꾸는 일은
그 의존성이 무엇을 반환하느냐일 뿐입니다.

**무엇이 공유이고 무엇이 갈리는가.** `contracts.py` · `adapters/` · `aggregate.py` 는
두 구현이 **함께** 씁니다 — 복사하지 마세요. `AssistantResponse` 8상태가 갈리면 두
결과를 나란히 놓을 좌표계가 사라져서 비교 자체가 무의미해집니다. 갈리는 것은 "능력을
어떻게 고르고 언제 멈추는가"뿐입니다: `planner.py` + `semantic.py` + `graph.py` 가
LangGraph 전략이고, `agent/` 가 그 셋을 통째로 대체합니다.

**기존 파일을 `langgraph/` 하위로 옮기지 않았습니다.** 대칭적이라 그게 맞아 보이지만
`orchestration.graph|planner|semantic|service|social` 을 import 하는 파일이 25개이고,
그중 `src/daengs_evals/router_benchmark/runner_v5~v8.py` 와 `tests/test_router_benchmark_v5~v8.py`
는 **동결된 벤치마크를 재현하는 코드**입니다. 경로를 건드리면 동결의 의미가 흐려집니다.
"""

from __future__ import annotations

from typing import Any, Protocol

from daengs_backend.config import OrchestratorKind, settings
from daengs_backend.orchestration.contracts import AssistantResponse, PrincipalContext
from daengs_backend.orchestration.service import AssistantOrchestrationService


class Orchestrator(Protocol):
    """두 구현이 함께 지키는 유일한 모양.

    이 서명이 곧 비교의 좌표계입니다. 넓히고 싶어지면 그 전에 "그 인자를 두 구현이
    같은 뜻으로 쓸 수 있는가"를 물으세요 — 한쪽에만 의미가 있는 인자를 여기 두면
    그 시점부터 두 구현은 나란히 잴 수 없는 다른 서비스가 됩니다.
    """

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
    ) -> AssistantResponse: ...


def build_orchestrator(kind: OrchestratorKind | None = None) -> Orchestrator:
    """`kind` 가 없으면 `DAENGS_ORCHESTRATOR` 를, 있으면 그것을 따릅니다.

    **`kind` 를 인자로 받는 것이 요점입니다.** 모듈 최상단에서 `settings` 를 읽어
    구현을 고정하면 한 프로세스 안에 두 구현을 나란히 세울 수 없습니다. 비교
    벤치마크가 하는 일이 정확히 그것입니다 — 환경 변수를 토글하며 서버를 두 번
    띄우는 것이 아니라, 같은 골드 질의를 객체 두 개에 먹입니다:

        for k in ("langgraph", "agent"):
            orch = build_orchestrator(k)

    그래서 **배포 스위치(설정값)와 비교 스위치(이 인자)는 다른 메커니즘**입니다.
    """
    selected = kind or settings.orchestrator
    if selected == "langgraph":
        return AssistantOrchestrationService()
    if selected == "agent":
        # LangChain 은 `agent` extra 라 기본 설치에 없습니다. 그래서 이 갈래만
        # 지연 import 입니다 — 최상단에 두면 extra 없이는 backend 가 아예 안 뜹니다.
        from daengs_backend.orchestration.agent import AgentOrchestrationService

        return AgentOrchestrationService()
    raise ValueError(f"알 수 없는 오케스트레이터입니다: {selected!r}")


__all__ = ["Orchestrator", "OrchestratorKind", "build_orchestrator"]
