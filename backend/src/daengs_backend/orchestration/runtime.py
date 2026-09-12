"""`/assistant/query` 뒤의 오케스트레이터 구현을 만드는 자리.

한때 LangGraph 와 LangChain 에이전트 두 구현을 병존시키고 재 본 뒤 고르는 자리였습니다
(D-055). 비교 v2 는 정확도 우위를 못 보였고 토큰·지연은 에이전트가 약 두 배였으며,
승인된 후속 기능 어디에도 "툴 결과를 보고 다음 수를 정하는 선택" 이 필요하지 않아
그 조건이 채워지지 않은 채로 남았습니다. 그래서 LangGraph 를 유일한 지원 런타임으로
확정하고 에이전트 구현을 지웠습니다 (D-072). 비교 근거는
`backend/evals/orchestration_router/comparison_v2_report.md` 에 남아 있습니다.

**갈아끼울 지점이 여기라는 사실은 그대로 남깁니다.** `routers/assistant.py` 는
`run(...) -> AssistantResponse` 만 보고 구현을 모르므로, 나중에 두 번째 구현이 필요해지면
(D-072 재검토 조건 참고) 다시 여기 하나만 고치면 됩니다.

**공유 경계.** `contracts.py` · `adapters/` · `aggregate.py` 는 `planner.py` · `semantic.py` ·
`graph.py` 와 이미 한 몸입니다 — 이제 지킬 다른 구현이 없어도, 이 넷을 갈라 두는 이유
(계약 하나·집계 진리표 하나)는 그대로 유효합니다.

**기존 파일을 `langgraph/` 하위로 옮기지 않은 이유는 여전합니다.**
`orchestration.graph|planner|semantic|service|social` 을 import 하는 파일이 25개이고,
그중 `src/daengs_evals/router_benchmark/runner_v5~v8.py` 와 `tests/test_router_benchmark_v5~v8.py`
는 **동결된 벤치마크를 재현하는 코드**입니다. 경로를 건드리면 동결의 의미가 흐려집니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from daengs_backend.orchestration.contracts import AssistantResponse, PrincipalContext
from daengs_backend.orchestration.resolver import PendingClarification, PriorTurn
from daengs_backend.orchestration.service import AssistantOrchestrationService


class Orchestrator(Protocol):
    """`/assistant/query` 가 기대하는 유일한 모양.

    구현이 하나뿐이어도 이 Protocol 을 남겨 두는 이유는 `routers/assistant.py` 의
    `get_assistant_orchestration_service` 가 여기서 갈아끼워지고, 테스트가 이 자리를
    의존성 오버라이드로 쓰기 때문입니다 — 라우터가 구체 클래스를 몰라야 그 자리가 유지됩니다.
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
        prior_turns: Sequence[PriorTurn] = (),
        pending_clarification: PendingClarification | None = None,
    ) -> AssistantResponse: ...


def build_orchestrator() -> Orchestrator:
    """LangGraph 구현을 만듭니다. 요청마다(또는 호출마다) 새 인스턴스입니다.

    한때 `kind` 인자를 받아 비교 벤치마크가 같은 프로세스에서 두 구현을 나란히 세웠지만,
    비교가 끝나 에이전트 구현이 지워지면서(D-072) 그 인자도 같이 없앴습니다 — 고를 것이
    하나뿐이면 고르는 인자는 죽은 코드입니다.
    """
    return AssistantOrchestrationService()


__all__ = ["Orchestrator", "build_orchestrator"]
