"""Resolver 가 오케스트레이션의 어느 자리에 서는가.

수용 케이스 8(응급 우선)과 9(확신이 낮으면 되묻기)가 여기서 걸린다. `_service_with` 는
`tests/test_orchestration_semantic_router.py` · `test_orchestration_general_fallback.py` 가
오케스트레이터를 조립하는 방식을 그대로 따른다 — 진짜 Gemini 호출은 어디에도 없다.
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
from daengs_backend.orchestration.resolver import PriorTurn, ResolvedTurn, TurnRelation
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
    calls: list[str] = []

    class _Spy:
        async def resolve(self, **kwargs: object) -> ResolvedTurn:
            calls.append("resolved")
            raise AssertionError("emergency must short-circuit before resolution")

    service = _service_with(resolver=_Spy())
    response = await service.run(
        query="강아지가 초콜릿을 먹었어 지금 어떡해",
        principal=PRINCIPAL,
        prior_turns=[_turn("산책 코스 추천해줘", "근처 공원을 추천합니다.")],
    )
    assert calls == []
    assert response.status is not AssistantStatus.FAILED


# ---------------------------------------------------------------- 수용 케이스 9


async def test_low_confidence_asks_instead_of_guessing(monkeypatch) -> None:
    """수용 케이스 9 — 확정 못 하는 '그거' 는 임의로 잇지 않는다.

    **Resolver 는 CLARIFY 를 만들지 않는다.** 붙임을 버리고 `relation=NEW` 로 내려보내는
    것과 바이트 동일하게, General 의 payload 에는 아무 `conversation` 도 안 실린다 —
    General 의 기존 ask 경로가 오늘 하던 대로 되묻는다. 생산자는 둘로 유지된다.
    """
    monkeypatch.setattr(settings, "general_fallback", True)

    class _Unsure:
        async def resolve(self, **kwargs: object) -> ResolvedTurn:
            return ResolvedTurn(
                relation=TurnRelation.FOLLOW_UP,
                current_query="그거 얼마나 자주 해?",
                resolution_confidence=0.2,
                ambiguity="어느 것을 가리키는지 후보에서 못 찾았습니다",
            )

    captured: dict = {}
    service = _service_with(
        resolver=_Unsure(),
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


# ---------------------------------------------------------------- Resolver 실패


async def test_resolution_failure_degrades_to_todays_behaviour() -> None:
    """Resolver 가 죽어도 답은 나간다 — 이력 기능이 없던 때와 같게 돈다."""

    class _Broken:
        async def resolve(self, **kwargs: object) -> ResolvedTurn:
            from daengs_backend.orchestration.resolver import TurnResolutionError

            raise TurnResolutionError("down")

    service = _service_with(
        resolver=_Broken(),
        router_outputs=(json.dumps({"execute": ["training"], "handoffs": []}),),
    )
    response = await service.run(
        query="그거 얼마나 자주 해?",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        prior_turns=[_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")],
    )
    assert response.status is not AssistantStatus.FAILED
