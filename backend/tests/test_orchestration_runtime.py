"""`build_orchestrator()` 가 구현을 고르는 규칙.

여기서 지키는 것은 세 가지입니다.

1. 기본값은 LangGraph 다 — `DAENGS_ORCHESTRATOR` 를 안 건드린 서버가 지금과 똑같이 돈다.
2. **`kind` 인자가 설정값을 이긴다** — 이게 비교 벤치마크가 서는 자리입니다. 한
   프로세스 안에서 두 구현을 나란히 만들 수 있어야 같은 골드 질의를 둘에 먹입니다.
   설정을 읽어 고정해 버리면 서버를 두 번 띄우는 비교밖에 못 합니다.
3. 모르는 값은 **분명하게** 실패한다 — 조용히 LangGraph 로 되돌아가면, 오타 난 설정으로
   한 실험 결과를 나중에 진짜라고 믿게 됩니다.
"""

from __future__ import annotations

import pytest

from daengs_backend.config import settings
from daengs_backend.orchestration.runtime import build_orchestrator
from daengs_backend.orchestration.service import AssistantOrchestrationService


def test_default_is_langgraph() -> None:
    assert settings.orchestrator == "langgraph"
    assert isinstance(build_orchestrator(), AssistantOrchestrationService)


def test_explicit_langgraph_kind() -> None:
    assert isinstance(build_orchestrator("langgraph"), AssistantOrchestrationService)


def test_kind_argument_beats_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """설정이 agent 여도 `kind="langgraph"` 면 LangGraph 가 나온다.

    벤치마크가 의존하는 성질입니다. 반대 방향(설정 langgraph + kind agent)은 카드 ②가
    들어오기 전까지 NotImplementedError 로만 확인할 수 있어 아래 테스트가 겸합니다.
    """
    monkeypatch.setattr(settings, "orchestrator", "agent")
    assert isinstance(build_orchestrator("langgraph"), AssistantOrchestrationService)


def test_agent_is_not_built_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    """아직 없는 구현을 조용히 LangGraph 로 바꿔치기하지 않는다.

    설정으로 골랐든 인자로 골랐든 같은 자리에서 걸려야 합니다 — 설정 경로만 열려
    있으면 `.env` 에 agent 를 적어 둔 서버가 LangGraph 로 돌면서 결과만 남깁니다.
    """
    with pytest.raises(NotImplementedError):
        build_orchestrator("agent")

    monkeypatch.setattr(settings, "orchestrator", "agent")
    with pytest.raises(NotImplementedError):
        build_orchestrator()


def test_unknown_kind_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env` 오타는 Literal 이 기동 때 잡지만, 설정을 우회해 들어오는 값도 있다."""
    monkeypatch.setattr(settings, "orchestrator", "langraph")  # 흔한 오타
    with pytest.raises(ValueError, match="langraph"):
        build_orchestrator()


def test_new_instance_per_call() -> None:
    """요청마다 새로 만든다 — 두 구현을 나란히 세울 때 상태가 새지 않아야 한다."""
    assert build_orchestrator("langgraph") is not build_orchestrator("langgraph")
