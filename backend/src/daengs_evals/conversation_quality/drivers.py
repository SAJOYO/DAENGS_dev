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
    #: **`"fake"` 를 쓰지 않는다.** 그것은 `collect.AdapterMode` 의 `"fake"`(진짜
    #: 오케스트레이터 + 가짜 capability 어댑터)가 이미 쓰는 값이다 — 여기서 같은 문자열을
    #: 쓰면 오케스트레이터를 통째로 건너뛴 이 드라이버의 행과 진짜 오케스트레이터를 돌린
    #: 랩이 헤더의 `adapter_mode` 만으로는 구별되지 않는다. `render_compare` 가 그 둘을 같은
    #: 것으로 여기고 비교를 허락하면, 두 랩 사이의 가장 큰 차이가 핀 위에서 안 보이게 된다.
    #: CLI 의 `--adapter-mode fake-driver` 선택지와 같은 이름을 쓴다.
    adapter_mode = "fake-driver"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.seen_payloads: list[dict] = []
        #: `StatelessDriver` 와 같은 자리 — `collect.py` 가 호출 전에 갈아 끼운다. `send()` 는
        #: 이 값을 읽지 않는다(가짜라 라우팅할 것이 없다), 그래도 속성이 있어야 `collect.py`
        #: 가 "이 드라이버는 상태를 받는다" 고 판단해 `state_supplied` 를 채운다.
        self.context: dict = {}

    def send(self, query: str) -> dict:
        self.seen_payloads.append({"query": query})
        # 오늘의 런타임과 같은 진실 — 이전 턴은 안 싣는다. 하드코딩이 아니라 이 드라이버가
        # 실제로 보낸 것을 그대로 말하는 것이다(`StatelessDriver.send` 와 같은 값).
        return {"message": self._replies.pop(0), "status": "ANSWERED", "prior_turns_supplied": []}


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
            "route_plan": _sanitize_route_plan(plan) if plan not in (None, NOT_REACHED) else plan,
            "general_decision": general_decision,
            # `send()` 는 질의 하나만 보낸다 — 이전 턴을 실은 적이 없으므로 이 드라이버가
            # 정직하게 말할 수 있는 값은 늘 빈 리스트다. `collect.py` 는 이 값을 하드코딩하지
            # 않고 여기서 읽는다 — `SessionDriver` 가 이력을 실으면 이 자리만 달라진다.
            "prior_turns_supplied": [],
        }


def _sanitize_route_plan(plan: Any) -> dict[str, Any]:
    """`RoutePlan` 에서 라우팅 메타데이터만 남기고 `requests[].payload` 는 버린다.

    `CapabilityRequest.payload` 는 `DogContext`(견종 · 개월령 · 급여 방식 · 병력 · 투약 여부)
    를 실어 나른다 — 그대로 덤프하면 `real` 어댑터로 한 번만 돌려도 실견 프로필이 랩 파일에
    박힌다("개인정보를 로그에 남기지 않는다" 위반). 질의는 `TurnSnapshot.query` 에, 공급된
    상태는 `TurnSnapshot.state_supplied` 에 이미 따로 있으니 여기서 payload 를 버려도 잃는
    정보가 없다. **편의로라도 다시 넣지 말 것** — 다음에 이 자리를 만지는 사람에게 남기는 경고.
    """
    return {
        "router": plan.router.value if hasattr(plan.router, "value") else str(plan.router),
        "model": plan.model,
        "prompt_version": plan.prompt_version,
        "capabilities": [r.capability.value for r in plan.requests],
        "handoffs": [h.model_dump(mode="json") for h in plan.handoffs],
        "clarify_requested": plan.clarify is not None,
    }
