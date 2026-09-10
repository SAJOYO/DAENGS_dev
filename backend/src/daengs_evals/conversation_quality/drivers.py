"""케이스를 런타임에 재생하는 이음매.

**이 이음매가 전후 비교를 가능하게 한다.** 오늘 런타임은 무상태라 `StatelessDriver`
가 턴마다 독립 호출을 한다. 이력 기제가 생기면 `SessionDriver` 를 더하고 **하네스는
안 고친다** — 그래야 두 랩에서 케이스 · 판정 · 리포트가 같은 물건으로 남는다.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

#: `send()` 가 돌려주는 payload 에 이 키가 없으면 "이 드라이버로는 안 닿는다" 는 뜻이다.
#: `None` 은 닿았는데 값이 비었다는 뜻이라 서로 다르다 — `collect.py` 가 이 차이로
#: "값 없음(안 해당)" 과 "이 이음매로는 모름" 을 가른다.
NOT_REACHED = "unavailable-via-driver"


class ConversationDriver(Protocol):
    def send(self, query: str) -> dict: ...


class FakeDriver:
    """테스트용. 실제 호출을 하지 않는다."""

    #: 랩 헤더가 어댑터 출처를 적을 자리 — `FakeDriver` 는 늘 가짜다.
    adapter_mode = "fake"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.seen_payloads: list[dict] = []

    def send(self, query: str) -> dict:
        self.seen_payloads.append({"query": query})
        return {"message": self._replies.pop(0), "status": "ANSWERED"}


class StatelessDriver:
    """오늘의 런타임을 그대로 흉내낸다.

    `routers/assistant.py` 가 `service.run(query=...)` 로 현재 질의 하나만 넘기는 것과 같은
    모양이다 — 이전 턴을 담아 보내지 않는다. 그래서 이 드라이버로 모은 랩에서
    `context_continuity` · `repair_success` 가 0 인 것은 판정기의 발견이지 드라이버의
    결함이 아니다.

    `plan_sink` · `general_sink` 는 `orchestrator_comparison.runner_v2.RecordingEngine` 과
    같은 자리 — 엔진 · 어댑터에 닿은 값을 얕게 가로챈다. 둘 다 `collect.py` 가 오케스트레이터를
    조립하며 만들어 넘긴다. 이 드라이버는 그 조립 방법을 모른다 — 그래야 `SessionDriver` 가
    같은 조립 위에 얹혀도 이 클래스를 안 건드린다.
    """

    def __init__(
        self,
        orchestrator: Any,
        *,
        principal: Any,
        adapter_mode: str,
        plan_sink: dict[str, Any] | None = None,
        general_sink: dict[str, Any] | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._principal = principal
        self.adapter_mode = adapter_mode
        self._plan_sink = plan_sink
        self._general_sink = general_sink
        #: `send()` 가 매번 다시 보내는 구조화 상태. `ConversationDriver.send` 는 질의 하나만
        #: 받으므로(그것이 오늘 런타임과 같은 계약이다), 상태는 호출 전에 이 속성으로 얹는다.
        #: `collect.py` 가 케이스마다 이 값을 `case.state_snapshot` 으로 갈아 끼운다.
        self.context: dict = {}

    def send(self, query: str) -> dict:
        if self._plan_sink is not None:
            self._plan_sink["plan"] = None
        if self._general_sink is not None:
            self._general_sink["decision"] = None
        response = asyncio.run(
            self._orchestrator.run(query=query, principal=self._principal, context=self.context)
        )
        capability = response.results[0].capability.value if response.results else None
        plan = self._plan_sink.get("plan") if self._plan_sink is not None else NOT_REACHED
        general_decision = (
            self._general_sink.get("decision") if self._general_sink is not None else NOT_REACHED
        )
        return {
            "status": response.status.value,
            "message": response.message,
            "capability": capability,
            "route_plan": plan.model_dump(mode="json") if plan not in (None, NOT_REACHED) else plan,
            "general_decision": general_decision,
        }
