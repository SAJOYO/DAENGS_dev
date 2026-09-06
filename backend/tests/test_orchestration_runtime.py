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

import sys

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

    벤치마크가 의존하는 성질입니다. 반대 방향(설정 langgraph + kind agent)은 아래
    `test_agent_kind_builds_the_agent` 가 겸합니다.
    """
    monkeypatch.setattr(settings, "orchestrator", "agent")
    assert isinstance(build_orchestrator("langgraph"), AssistantOrchestrationService)


def test_agent_kind_builds_the_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """설정으로 골랐든 인자로 골랐든 같은 구현이 나온다.

    한쪽 경로만 열려 있으면 `.env` 에 agent 를 적어 둔 서버가 LangGraph 로 돌면서
    결과만 남깁니다 — 그 결과를 나중에 에이전트 것이라고 믿게 됩니다.
    """
    pytest.importorskip("langchain")
    from daengs_backend.orchestration.agent import AgentOrchestrationService

    assert isinstance(build_orchestrator("agent"), AgentOrchestrationService)

    monkeypatch.setattr(settings, "orchestrator", "agent")
    assert isinstance(build_orchestrator(), AgentOrchestrationService)


def test_agent_extra_missing_is_not_silently_langgraph() -> None:
    """`agent` extra 가 없는 환경에서도 LangGraph 로 되돌아가지 않는다.

    설치가 안 됐으면 ImportError 로 시끄럽게 실패해야 합니다. 조용히 LangGraph 를
    주면 CI 나 서버에서 "에이전트로 돌렸다"는 기록만 남고 실제로는 안 돈 것이 됩니다.
    """
    pytest.importorskip("langchain")
    assert not isinstance(build_orchestrator("agent"), AssistantOrchestrationService)


def test_missing_agent_extra_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """extra 가 없으면 **구현을 고르는 순간** 실패한다 — 요청 때가 아니라.

    `agent/service.py` 가 LangChain 을 모듈 최상단에서 import 하는 이유입니다. 거기서도
    미루면, extra 없이 `DAENGS_ORCHESTRATOR=agent` 로 뜬 서버가 멀쩡히 기동한 뒤 모든
    요청을 "잠시 후 다시 시도해 주세요"로 돌려보냅니다 — 설치가 빠진 것이 프로바이더
    장애처럼 보이는, `/life/ask` 만 503 이던 그 모양입니다.

    `sys.modules` 에 None 을 꽂아 import 실패를 흉내냅니다.
    """
    monkeypatch.setitem(sys.modules, "daengs_backend.orchestration.agent", None)
    with pytest.raises(ImportError):
        build_orchestrator("agent")


def test_unknown_kind_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env` 오타는 Literal 이 기동 때 잡지만, 설정을 우회해 들어오는 값도 있다."""
    monkeypatch.setattr(settings, "orchestrator", "langraph")  # 흔한 오타
    with pytest.raises(ValueError, match="langraph"):
        build_orchestrator()


def test_new_instance_per_call() -> None:
    """요청마다 새로 만든다 — 두 구현을 나란히 세울 때 상태가 새지 않아야 한다."""
    assert build_orchestrator("langgraph") is not build_orchestrator("langgraph")
