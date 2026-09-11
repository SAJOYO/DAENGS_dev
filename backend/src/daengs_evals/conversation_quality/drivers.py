"""케이스를 런타임에 재생하는 이음매.

**이 이음매가 전후 비교를 가능하게 한다.** 오늘 런타임은 무상태라 `StatelessDriver`
가 턴마다 독립 호출을 한다. `#416` 이 Turn Resolver 를 실제로 놓으면서 `SessionDriver`
가 여기 더해졌다 — **하네스는 안 고쳤다**: `collect.py` 는 `build_session_driver` 한
함수만 늘었고 `target_turn_row` 는 그대로다. 두 드라이버가 나란히 있는 것도 그대로다
(상속하지 않는다) — 각자 자기가 실제로 보낸 것만 정직하게 말해야, 두 랩에서 케이스 ·
판정 · 리포트가 같은 물건으로 남는다.
"""

from __future__ import annotations

import asyncio
import uuid
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

    #: `run_collect` 가 `LapHeader.driver` 에 그대로 옮긴다(`adapter_mode` 와 같은 자리) —
    #: `report.render_compare` 가 "이 before 랩이 이전 턴을 실었는지" 를 축 이름만으로
    #: 짐작하지 않고 여기서 읽게 하려는 것이다 (R25). `SessionDriver.driver_kind` 와 짝.
    driver_kind = "stateless"

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
            # 되묻기의 **구조화된 흔적** (#415). `message` 에도 같은 질문이 들어 있지만,
            # 리포트가 "무엇을 물었나" 를 한국어에서 다시 파싱하지 않게 따로 싣는다.
            # `#416` 의 `SessionDriver` 도 이 자리를 그대로 쓴다.
            "clarify": _sanitize_clarify(response.clarify),
            # `send()` 는 질의 하나만 보낸다 — 이전 턴을 실은 적이 없으므로 이 드라이버가
            # 정직하게 말할 수 있는 값은 늘 빈 리스트다. `collect.py` 는 이 값을 하드코딩하지
            # 않고 여기서 읽는다 — `SessionDriver` 가 이력을 실으면 이 자리만 달라진다.
            "prior_turns_supplied": [],
        }


class SessionDriver:
    """이력 기제(#416 Turn Resolver)를 실제로 태운다. `StatelessDriver` **옆**에 둔다.

    `send()` 가 지금까지 오간 턴을 `PriorTurn` 후보로 쌓아 `orchestrator.run(...,
    prior_turns=…, pending_clarification=…)` 로 보낸다 — **여기서 끝나는 유사성이다.**
    `services/chat.py` 는 `candidates_of` · `pending_clarification_of` 를 부를 때마다
    `chat_repo.list_turns(session, session_id, …)` 로 DB 에서 **그 세션의** 턴만 읽는다 —
    대화 하나(`session_id`)가 스코프 그 자체다. 이 클래스는 그 스코프를 모른다: `_history` ·
    `_pending` 은 이 **드라이버 인스턴스**에 그냥 쌓일 뿐이고, 인스턴스가 몇 개의 대화를
    나르는지는 이 클래스가 알 방법이 없다(#446) — 한 인스턴스로 케이스 여러 개를 돌리면
    앞 케이스의 이력이 다음 케이스로 그대로 넘어간다. 케이스 경계를 가르는 것은 이 클래스의
    일이 아니라 `reset_for_new_case()` 를 부르는 쪽(`collect.run_collect`)의 일이다 — DB 가
    `session_id` 로 격리해 주는 것을 여기서는 호출자가 대신 해 줘야 한다는 뜻이다.
    (랩 수집은 세션 하나를 한 번에 돌 뿐 DB 에 남기지 않는다 — D-037 · D-048, 대화 원문을
    로그·랩에 안 남긴다.)

    반환 payload 는 `StatelessDriver` 와 **같은 칸**을 쓴다 — 리포트 · 비교 코드가 드라이버
    종류를 몰라도 되게 하려는 것이다(`collect.target_turn_row` 가 이 계약에 기댄다).
    `clarify` 자리는 `_sanitize_clarify` 를 그대로 쓴다 — 화이트리스트가 하나뿐이면
    두 드라이버가 몰래 갈라질 길이 없다.

    `prior_turns_supplied` 에는 **인덱스만** 적는다 — `[0, 1, …]` 처럼 이번 호출에 실제로
    실어 보낸 이전 턴의 순번이다. 케이스 원문은 이미 `cases_v1.jsonl` 에 있으므로 텍스트를
    다시 적으면 얻는 정보 없이 새는 정보만 생긴다(`_sanitize_route_plan` 이 `payload` 를
    버리는 것과 같은 원칙). `PriorTurn.turn_id` 도 랩에는 안 실린다 — 이 프로세스 안에서만
    쓰는 합성 uuid 라 밖에서는 의미가 없다.
    """

    #: `StatelessDriver.driver_kind` 와 짝 — `run_collect` 가 이 값을 `LapHeader.driver` 에
    #: 그대로 옮긴다. `render_compare` 가 `context_continuity`·`repair_success` 를 0 으로
    #: 못박는 것은 **before 랩이 `stateless` 일 때뿐**이다(R25) — 이 랩이 이전 턴을 실제로
    #: 실어 보냈으므로 그 못박음을 걸면 안 된다.
    driver_kind = "session"

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
        #: `StatelessDriver` 와 같은 자리 — `collect.py` 가 케이스마다 갈아 끼운다.
        self.context: dict = {}
        #: 이 세션에서 지금까지 완료된 턴. 인덱스 순서 그대로가 `prior_turns_supplied` 의
        #: 뜻이다 — 재정렬하지 않는다(`candidates_of` 와 같은 규칙).
        self._history: list[Any] = []
        #: 가장 최근 완료 턴이 `CLARIFY` 로 끝났을 때만 값이 있다 — `pending_clarification_of`
        #: 와 같은 "한 턴짜리 대기" 규칙. 다음 `send()` 가 답을 받으면 비운다.
        self._pending: Any = None
        #: 테스트 · 사람이 "지금 되묻기가 대기 중인가" 를 텍스트 없이 확인할 자리.
        #: 질문 문장 자체는 `payload["clarify"]["question"]` 에도 있으므로 새 정보는 아니다.
        self.last_pending_question: str | None = None

    def reset_for_new_case(self) -> None:
        """다음 케이스가 완전히 새 대화라는 뜻으로 이력·대기를 비운다 (#446).

        `services/chat.py` 는 DB 에서 `session_id` 로 걸러 읽으니 이 비우기가 필요 없다 —
        여기는 메모리에 쌓기만 하는 클래스라 대신 호출자가 경계를 그어야 한다.
        `collect.run_collect` 가 케이스마다 이 메서드를 부른다(있으면 — `getattr` 로 찾으므로
        없는 드라이버는 그냥 지나간다). 잊으면 `_history` 가 다음 케이스로 새고, 그 결과가
        정확히 이 카드가 실측으로 잡은 결함이다: 무관한 케이스의 앞 턴을 이어받아 되묻는다.
        """
        self._history = []
        self._pending = None
        self.last_pending_question = None

    def send(self, query: str) -> dict:
        # 늦게 import — 모듈 최상단에서 물면 이 패키지를 import 만 해도
        # `daengs_backend.config`(DB 접속 정보 · 암호화 키)가 있어야 한다
        # (`test_every_module_in_the_package_imports_without_backend_settings`).
        from daengs_backend.orchestration.resolver import PendingClarification, PriorTurn

        if self._plan_sink is not None:
            self._plan_sink["plan"] = None
        if self._general_sink is not None:
            self._general_sink["decision"] = None
        prior_turns = list(self._history)
        pending_clarification = self._pending
        response = asyncio.run(
            self._orchestrator.run(
                query=query,
                principal=self._principal,
                context=self.context,
                prior_turns=prior_turns,
                pending_clarification=pending_clarification,
            )
        )
        capability = response.results[0].capability.value if response.results else None
        plan = self._plan_sink.get("plan") if self._plan_sink is not None else NOT_REACHED
        general_decision = (
            self._general_sink.get("decision") if self._general_sink is not None else NOT_REACHED
        )
        turn_id = uuid.uuid4()
        self._history.append(
            PriorTurn(turn_id=turn_id, user=query, assistant=response.message or "")
        )
        if response.status.value == "CLARIFY" and response.clarify is not None:
            self._pending = PendingClarification(
                turn_id=turn_id,
                question=response.clarify.question,
                missing=list(response.clarify.missing),
                missing_axes=list(response.clarify.missing_axes),
            )
            self.last_pending_question = response.clarify.question
        else:
            self._pending = None
            self.last_pending_question = None
        return {
            "status": response.status.value,
            "message": response.message,
            "capability": capability,
            "route_plan": _sanitize_route_plan(plan) if plan not in (None, NOT_REACHED) else plan,
            "general_decision": general_decision,
            "clarify": _sanitize_clarify(response.clarify),
            # 텍스트가 아니라 순번 — 모듈 docstring 참고. `range(len(prior_turns))` 라
            # 이번 호출에 실제로 실어 보낸 것과 늘 같은 길이다(하드코딩이 아니다).
            "prior_turns_supplied": list(range(len(prior_turns))),
        }


def _sanitize_clarify(clarify: Any) -> dict[str, Any] | None:
    """`ClarifyRequest` 에서 리포트가 읽는 세 칸만. 없으면 `None`.

    `_sanitize_route_plan` 과 같은 원칙으로 **화이트리스트**다 — 계약이 나중에 칸을 늘려도
    랩 파일에 뜻하지 않은 값이 실리지 않는다. 질문 문장은 이미 `TurnSnapshot.message` 에
    있는 것과 같은 텍스트라 새로 새는 정보가 없다.
    """
    if clarify is None:
        return None
    return {
        "question": clarify.question,
        "missing": list(clarify.missing),
        # `ObservationAxis` 는 `StrEnum` 이라 그대로 두면 JSON 에 값이 실린다. 다만
        # `json.dumps` 가 아니라 리포트가 문자열로 비교하므로 명시적으로 풀어 둔다.
        "missing_axes": [str(axis) for axis in clarify.missing_axes],
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
