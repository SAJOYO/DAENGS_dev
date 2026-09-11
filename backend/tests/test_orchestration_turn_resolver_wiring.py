"""Resolver 가 오케스트레이션의 어느 자리에 서는가.

수용 케이스 8(응급 우선)과 9(확신이 낮으면 되묻기)가 여기서 걸린다. `_service_with` 는
`tests/test_orchestration_semantic_router.py` · `test_orchestration_general_fallback.py` 가
오케스트레이터를 조립하는 방식을 그대로 따른다 — 진짜 Gemini 호출은 어디에도 없다.

**코디네이터 리뷰 fix round 1 이 추가한 것 (Important 1~3):** 양성 배선(붙임이 실제로
`GeneralPayload.conversation` 까지 도달하는 경로), 리졸버가 받는 인자 자체(`candidates`·
`pending`), 결정론 라우팅이 리졸버보다 앞선다는 계약. 전부 지금까지 빠져 있었다 —
`resolved=conversation` 인자를 지워도, `candidates=()` 로 바꿔치기해도 옛 스위트는 초록이었다.
"""

from __future__ import annotations

import json
import uuid

from daengs_backend.config import settings
from daengs_backend.orchestration.contracts import (
    AssistantStatus,
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    PrincipalContext,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.resolver import (
    PendingClarification,
    PriorTurn,
    ResolvedTurn,
    TurnRelation,
    TurnResolutionError,
)
from daengs_backend.orchestration.semantic import GeminiSemanticRouter
from daengs_backend.orchestration.service import AssistantOrchestrationService

PRINCIPAL = PrincipalContext(subject="test-user", kind="APP_USER")
SEOUL = {"location": {"lat": 37.5, "lon": 127.0}}


def _turn(user: str, assistant: str) -> PriorTurn:
    return PriorTurn(turn_id=uuid.uuid4(), user=user, assistant=assistant)


class ScriptedTransport:
    """라우터에 줄 원답을 순서대로 낸다. 응급 테스트에서는 한 번도 안 불린다."""

    def __init__(self, *outputs: object) -> None:
        self.outputs = list(outputs)

    async def __call__(self, prompt: str) -> object:
        return self.outputs.pop(0)


class RecordingAdapter:
    """능력 하나의 호출을 기록만 하고 고정 답을 낸다."""

    def __init__(self, capability: CapabilityName, *, sink: dict | None = None) -> None:
        self.capability = capability
        self.calls: list[CapabilityRequest] = []
        self._sink = sink

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        self.calls.append(request)
        if self._sink is not None:
            self._sink["payload"] = request.payload
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={"answer": f"{self.capability.value} 답"},
            elapsed_ms=1,
        )


class RecordingResolver:
    """실제로 넘겨받은 kwargs 를 남긴다 (Important 2) — `**kwargs: object` 로 버리기만 하는
    가짜는 `service.py` 가 `candidates=()` 로 바꿔치기해도, `pending` 을 안 넘겨도 못 잡는다."""

    def __init__(self, outcome: ResolvedTurn | Exception) -> None:
        self.calls: list[dict[str, object]] = []
        self._outcome = outcome

    async def resolve(self, **kwargs: object) -> ResolvedTurn:
        self.calls.append(kwargs)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class RaisingResolver:
    """호출되면 그 자체가 실패다 — "리졸버가 이 경로에서는 아예 안 불려야 한다" 를 잰다."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def resolve(self, **kwargs: object) -> ResolvedTurn:
        self.calls.append(kwargs)
        raise AssertionError("resolver must not run on this path")


def _service_with(
    *,
    resolver: object,
    router_outputs: tuple[object, ...] = (),
    general_sink: dict | None = None,
) -> AssistantOrchestrationService:
    """Semantic 라우터 테스트들과 같은 조립 — `resolver` 만 이 테스트 파일 고유다."""
    adapters = {
        CapabilityName.TRAINING: RecordingAdapter(CapabilityName.TRAINING),
        CapabilityName.GENERAL: RecordingAdapter(CapabilityName.GENERAL, sink=general_sink),
        CapabilityName.VET_CONTACT: RecordingAdapter(CapabilityName.VET_CONTACT),
    }
    return AssistantOrchestrationService(
        engine=OrchestrationEngine(adapters),
        semantic_router=GeminiSemanticRouter(generate=ScriptedTransport(*router_outputs)),
        turn_resolver=resolver,
    )


# ---------------------------------------------------------------- 수용 케이스 8


async def test_emergency_wins_before_the_resolver_runs() -> None:
    """수용 케이스 8 — 과거 맥락과 무관하게 현재 발화의 응급 신호가 이긴다."""
    resolver = RaisingResolver()
    service = _service_with(resolver=resolver)
    response = await service.run(
        query="강아지가 초콜릿을 먹었어 지금 어떡해",
        principal=PRINCIPAL,
        prior_turns=[_turn("산책 코스 추천해줘", "근처 공원을 추천합니다.")],
    )
    assert resolver.calls == []
    assert response.status is not AssistantStatus.FAILED


# ---------------------------------------------------------- 계약 2 — 결정론도 앞이다


async def test_deterministic_route_wins_before_the_resolver_runs() -> None:
    """계약 2 — 명시 신호(`requested_capability`)도 응급과 같은 자리에 선다.

    공유 `if route_plan is None:` 가드를 쪼개서 응급만 리졸버를 건너뛰게 바꾸면, 이
    테스트가 빨갛게 죽는다 — 녹색 스위트만으로는 그 리팩터가 안전한지 못 가린다.
    """
    resolver = RaisingResolver()
    service = _service_with(resolver=resolver)
    response = await service.run(
        query="산책 중에 짖는 걸 어떻게 고쳐요?",
        principal=PRINCIPAL,
        requested_capability="training",
        prior_turns=[_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")],
    )
    assert resolver.calls == []
    assert response.status is not AssistantStatus.FAILED


# --------------------------------------------------- 리졸버가 실제로 받는 인자 (중요 2)


async def test_resolver_receives_the_exact_candidates_and_pending() -> None:
    """`service.py` 가 `candidates=()` 로 바꿔치기하거나 `pending` 을 빠뜨려도, `**kwargs`
    를 버리는 가짜로는 못 잡는다 — 실제로 받은 값을 기록해서 대조한다."""
    prior_turns = [_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")]
    pending = PendingClarification(turn_id=uuid.uuid4(), question="언제부터 그랬나요?")
    resolver = RecordingResolver(
        ResolvedTurn(
            relation=TurnRelation.NEW, current_query="다른 얘기", resolution_confidence=1.0
        )
    )
    service = _service_with(
        resolver=resolver, router_outputs=(json.dumps({"execute": ["training"], "handoffs": []}),)
    )
    await service.run(
        query="다른 얘기",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        prior_turns=prior_turns,
        pending_clarification=pending,
    )
    [call] = resolver.calls
    assert call["candidates"] == prior_turns
    assert call["pending"] is pending


# ---------------------------------------------------------------- 수용 케이스 9


async def test_low_confidence_asks_instead_of_guessing(monkeypatch) -> None:
    """수용 케이스 9 — 확정 못 하는 '그거' 는 임의로 잇지 않는다.

    **Resolver 는 CLARIFY 를 만들지 않는다.** 붙임을 버리고 `relation=NEW` 로 내려보내는
    것과 바이트 동일하게, General 의 payload 에는 아무 `conversation` 도 안 실린다 —
    General 의 기존 ask 경로가 오늘 하던 대로 되묻는다. 생산자는 둘로 유지된다.
    """
    monkeypatch.setattr(settings, "general_fallback", True)

    resolver = RecordingResolver(
        ResolvedTurn(
            relation=TurnRelation.FOLLOW_UP,
            current_query="그거 얼마나 자주 해?",
            resolution_confidence=0.2,
            ambiguity="어느 것을 가리키는지 후보에서 못 찾았습니다",
        )
    )

    captured: dict = {}
    service = _service_with(
        resolver=resolver,
        router_outputs=(json.dumps({"execute": [], "handoffs": []}),),
        general_sink=captured,
    )
    response = await service.run(
        query="그거 얼마나 자주 해?",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        prior_turns=[_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")],
    )
    assert response.status is not AssistantStatus.FAILED
    # 붙임이 버려져 아래로 아무것도 안 간다 — General 이 오늘처럼 되묻는다.
    assert captured["payload"].conversation is None


# --------------------------------------------------------- 양성 배선 (중요 1)


async def test_high_confidence_follow_up_reaches_the_general_payload(monkeypatch) -> None:
    """중요 1 — 붙임이 실제로 `GeneralPayload.conversation` 까지 도달하는지 아무도 안 쟀다.

    `service.py` 의 `resolved=conversation` 인자를 (시맨틱 라우터 호출이든
    `assemble_route_plan` 호출이든) 지워도 옛 스위트는 초록이었다 — 이 테스트가 그 자리다.
    """
    monkeypatch.setattr(settings, "general_fallback", True)
    referenced = _turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")
    resolver = RecordingResolver(
        ResolvedTurn(
            relation=TurnRelation.FOLLOW_UP,
            current_query="그거 얼마나 자주 바꿔야 해?",
            referenced_turn_id=referenced.turn_id,
            referenced_original_request=referenced.user,
            standalone_query="사료를 얼마나 자주 바꿔야 하나요?",
            resolution_confidence=0.95,
            context_used=[referenced.turn_id],
        )
    )
    captured: dict = {}
    service = _service_with(
        resolver=resolver,
        router_outputs=(json.dumps({"execute": [], "handoffs": []}),),
        general_sink=captured,
    )
    response = await service.run(
        query="그거 얼마나 자주 바꿔야 해?",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        prior_turns=[referenced],
    )

    assert response.status is not AssistantStatus.FAILED
    conversation = captured["payload"].conversation
    assert conversation is not None
    assert conversation.relation is TurnRelation.FOLLOW_UP
    assert conversation.standalone_query == "사료를 얼마나 자주 바꿔야 하나요?"
    assert conversation.referenced_original_request == referenced.user


# ---------------------------------------------------------------- Resolver 실패


async def test_resolution_failure_degrades_to_todays_behaviour() -> None:
    """Resolver 가 죽어도 답은 나간다 — 이력 기능이 없던 때와 같게 돈다."""
    resolver = RecordingResolver(TurnResolutionError("down"))
    service = _service_with(
        resolver=resolver,
        router_outputs=(json.dumps({"execute": ["training"], "handoffs": []}),),
    )
    response = await service.run(
        query="그거 얼마나 자주 해?",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        prior_turns=[_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")],
    )
    assert response.status is not AssistantStatus.FAILED


# ---------------------------------------------------------------- R16 킬 스위치


async def test_turn_resolver_off_skips_it_entirely(monkeypatch) -> None:
    """R16 — 꺼져 있으면 리졸버는 아예 안 불리고, 오늘처럼(이력 이어짐 없이) 답이 나간다."""
    monkeypatch.setattr(settings, "turn_resolver", False)
    resolver = RaisingResolver()
    service = _service_with(
        resolver=resolver,
        router_outputs=(json.dumps({"execute": ["training"], "handoffs": []}),),
    )
    response = await service.run(
        query="그거 얼마나 자주 해?",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        prior_turns=[_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")],
    )
    assert resolver.calls == []
    assert response.status is not AssistantStatus.FAILED
