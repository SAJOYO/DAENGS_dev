"""`build_orchestrator()` 가 LangGraph 구현을 만드는 것을 확인한다.

D-072 이전에는 `kind` 인자와 `DAENGS_ORCHESTRATOR` 설정이 LangGraph/에이전트 중 무엇을
고르는지를 이 파일이 재고 있었습니다. 에이전트 구현이 지워지면서 고를 것이 없어졌으므로
이 파일이 지키는 것도 하나로 줄었습니다: 새 인스턴스를 만든다는 것.
"""

from __future__ import annotations

from daengs_backend.orchestration.runtime import build_orchestrator
from daengs_backend.orchestration.service import AssistantOrchestrationService


def test_builds_the_langgraph_service() -> None:
    assert isinstance(build_orchestrator(), AssistantOrchestrationService)


def test_new_instance_per_call() -> None:
    """호출마다 새로 만든다 — 상태가 호출 사이에 새지 않아야 한다."""
    assert build_orchestrator() is not build_orchestrator()
