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
    ObservationAxis,
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
from daengs_backend.orchestration.semantic import (
    PROMPT_VERSION,
    RESOLVED_PROMPT_VERSION,
    GeminiSemanticRouter,
)
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


def _fixed(
    relation: TurnRelation, *, referenced: PriorTurn, standalone: str | None = None
) -> RecordingResolver:
    """관계를 고정한 가짜 resolver — 앞 turn 하나를 가리키는 판정만 흉내 낸다.

    수용 케이스 2·3·4·6 이 공유하는 모양: `referenced_turn_id`/`referenced_original_request`
    가 늘 같이 실리고(#416 Task 5 의 `conversation_context_of` 가 이 두 값과
    `standalone_query` 를 그대로 옮긴다), `pending_clarification_id` 는 없다 — 이 관계들은
    앞선 완료 turn 을 잇는 것이지 대기 중인 되묻기를 잇는 것이 아니다.
    """
    return RecordingResolver(
        ResolvedTurn(
            relation=relation,
            current_query=standalone or referenced.user,
            referenced_turn_id=referenced.turn_id,
            referenced_original_request=referenced.user,
            standalone_query=standalone,
            resolution_confidence=1.0,
            context_used=[referenced.turn_id],
        )
    )


def _fixed_pending(relation: TurnRelation, pending: PendingClarification) -> RecordingResolver:
    """대기 중인 되묻기에 앵커된 판정을 흉내 낸다 — `pending_clarification_id` 가
    `pending.turn_id` 와 실제로 같아야 `conversation_context_of` 가 앵커로 본다(R8).
    다르면(또는 없으면) `pending_question`/`pending_missing_axes` 는 조용히 비워진다 —
    그래서 이 헬퍼가 그 일치를 대신 보장한다."""
    return RecordingResolver(
        ResolvedTurn(
            relation=relation,
            current_query=pending.question,
            pending_clarification_id=pending.turn_id,
            pending_missing_axes=list(pending.missing_axes),
            resolution_confidence=1.0,
        )
    )


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


# ---------------------------------------------------------------- R18 (fix round 1)


async def test_resolved_prompt_version_reaches_the_route_plan(monkeypatch) -> None:
    """R18 — 실제로 `RESOLVED_PROMPT_VERSION` 프롬프트가 나간 turn 은 `RoutePlan.prompt_
    version`(→ `RouteTrace`, `_route_metadata`) 에도 그 값으로 남아야 한다. 전에는 세 곳
    모두 `semantic.PROMPT_VERSION` 을 하드코딩해서, 맥락이 실린 turn 도 평범한 `v10` 으로
    적혔다 — 평가 랩이 잘못된 핀을 신뢰 있는 것처럼 기록하는 사고."""
    monkeypatch.setattr(settings, "general_fallback", True)
    referenced = _turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")
    resolver = RecordingResolver(
        ResolvedTurn(
            relation=TurnRelation.FOLLOW_UP,
            current_query="그거 얼마나 자주 해?",
            referenced_turn_id=referenced.turn_id,
            referenced_original_request=referenced.user,
            resolution_confidence=0.95,
            context_used=[referenced.turn_id],
        )
    )
    service = _service_with(
        resolver=resolver, router_outputs=(json.dumps({"execute": [], "handoffs": []}),)
    )
    response = await service.run(
        query="그거 얼마나 자주 해?",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        prior_turns=[referenced],
        include_route_trace=True,
    )
    assert response.status is not AssistantStatus.FAILED
    assert response.route is not None
    assert response.route.prompt_version == RESOLVED_PROMPT_VERSION


async def test_base_prompt_version_reaches_the_route_plan_without_context() -> None:
    """같은 자리, 맥락이 안 붙는 turn — 오늘과 같은 `PROMPT_VERSION` 이 그대로 적힌다."""
    resolver = RecordingResolver(
        ResolvedTurn(
            relation=TurnRelation.NEW,
            current_query="산책 중에 짖는 걸 어떻게 고쳐요?",
            resolution_confidence=1.0,
        )
    )
    service = _service_with(
        resolver=resolver,
        router_outputs=(json.dumps({"execute": ["training"], "handoffs": []}),),
    )
    response = await service.run(
        query="산책 중에 짖는 걸 어떻게 고쳐요?",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        include_route_trace=True,
    )
    assert response.status is not AssistantStatus.FAILED
    assert response.route is not None
    assert response.route.prompt_version == PROMPT_VERSION


async def test_router_failure_trace_carries_the_resolved_version() -> None:
    """라우터 실패 조기 반환도 트레이스를 잃지 않아야 하고(기존 요구), 그 트레이스의
    버전도 실제로 라우터에 넘어간 `resolved` 를 반영해야 한다 — `semantic_trace` 를
    `conversation` 이 정해진 뒤로 옮긴 재배치가 조기 반환 경로를 깨지 않았는지 잰다."""
    referenced = _turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")
    resolver = RecordingResolver(
        ResolvedTurn(
            relation=TurnRelation.FOLLOW_UP,
            current_query="그거 얼마나 자주 해?",
            referenced_turn_id=referenced.turn_id,
            referenced_original_request=referenced.user,
            resolution_confidence=0.95,
            context_used=[referenced.turn_id],
        )
    )
    # 스키마에 안 맞는 원답 → 재시도 두 번 다 실패 → SemanticRoutingError.
    service = _service_with(resolver=resolver, router_outputs=("not json", "still not json"))
    response = await service.run(
        query="그거 얼마나 자주 해?",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        prior_turns=[referenced],
        include_route_trace=True,
    )
    assert response.status is AssistantStatus.FAILED
    assert response.route is not None
    assert response.route.prompt_version == RESOLVED_PROMPT_VERSION


# ------------------------------------------------------- 수용 케이스 2·3·4·5·6
#
# 여기서부터는 관계를 가짜 resolver 로 고정한 채 "그 관계가 아래에서 무엇을 바꾸는가" 만
# 본다 — 모델이 관계를 올바르게 판정하는지는 Task 9 의 평가 랩이 잰다. 다섯 테스트 모두
# `general_sink` 로 `GeneralPayload` 를 가로채, `ConversationContext` 의 어느 필드가
# 실제로 거기까지 도달했는지를 값으로 비교한다 — `is not None` 은 기본값이 이미 `None`
# 이 아닌 필드(`relation`)에서는 아무것도 증명하지 못하고(트랩 2), `status is not FAILED`
# 는 `RecordingAdapter` 가 관계와 무관하게 늘 `CapabilityStatus.OK` 를 내는 이 하네스에서
# 거의 항상 참이라(트랩 1) 보조 신호로만 남긴다.
#
# **커버리지 경계.** FOLLOW_UP(대명사)·CORRECTION·FOLLOW_UP(되묻기 뒤) 세 케이스는
# 행동까지 핀으로 고정한다. `test_repeat_relation_and_its_referenced_turn_reach_the_answerer`
# 와 `test_meta_relation_reaches_the_answerer_instead_of_falling_through` 둘은 그렇지 않다
# — `RecordingAdapter` 가 답변 문구의 차이를 표현할 수 없어서, 이 둘은 "실제 General
# 프롬프트가 필요로 할 재료(관계·참조 turn)가 도달했다" 까지만 고정한다. REPEAT 이 같은
# 거절을 반복하지 않는다는 것과 META 가 off_topic 으로 떨어지지 않는다는 것 — 그 행동
# 결과 자체는 이 저장소의 어떤 유닛 테스트도 아직 안 잰다. Task 9 의 평가 랩이 잰다.


async def test_follow_up_pronoun_reaches_the_answerer(monkeypatch) -> None:
    """수용 케이스 2 — '그거 얼마나 자주 해?' 가 앞 요청("심장사상충 예방약 먹여야 해?")에
    붙는다. `resolver.conversation_context_of` 가 `referenced_original_request` 를 안
    옮기거나, `service.py` 가 `assemble_route_plan`/시맨틱 라우터에 `resolved=conversation`
    대신 `None` 을 넘기면 이 assert 가 죽는다 — 필드의 기본값이 `None` 이라 값 자체를
    비교한다(트랩 2 회피)."""
    monkeypatch.setattr(settings, "general_fallback", True)
    prior = _turn("심장사상충 예방약 먹여야 해?", "네, 보통 한 달에 한 번 투여합니다.")
    captured: dict = {}
    service = _service_with(
        resolver=_fixed(
            TurnRelation.FOLLOW_UP,
            referenced=prior,
            standalone="심장사상충 예방약을 얼마나 자주 먹여?",
        ),
        router_outputs=(json.dumps({"execute": [], "handoffs": []}),),
        general_sink=captured,
    )
    response = await service.run(
        query="그거 얼마나 자주 해?",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        prior_turns=[prior],
    )
    assert response.status is not AssistantStatus.FAILED
    assert captured["payload"].conversation.referenced_original_request == prior.user


async def test_correction_hands_the_corrected_frame_down(monkeypatch) -> None:
    """수용 케이스 3 — '아니, 산책 말고 밥' 의 정정된 프레임(축 자체가 바뀐 새 질문)이
    아래로 간다. `service.py`/`conversation_context_of` 가 `standalone_query` 나
    `referenced_original_request` 대신 원래 turn 의 값을 그대로 흘리면, 또는 `relation`
    을 CORRECTION 이 아니라 NEW 로 바꿔치기하면 아래 세 assert 중 하나가 죽는다."""
    monkeypatch.setattr(settings, "general_fallback", True)
    prior = _turn("산책 얼마나 시켜야 해?", "하루 30분 이상을 권합니다.")
    captured: dict = {}
    service = _service_with(
        resolver=_fixed(
            TurnRelation.CORRECTION, referenced=prior, standalone="밥을 얼마나 줘야 해?"
        ),
        router_outputs=(json.dumps({"execute": [], "handoffs": []}),),
        general_sink=captured,
    )
    response = await service.run(
        query="아니, 산책 말고 밥",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        prior_turns=[prior],
    )
    assert response.status is not AssistantStatus.FAILED
    conversation = captured["payload"].conversation
    assert conversation.relation is TurnRelation.CORRECTION
    assert conversation.standalone_query == "밥을 얼마나 줘야 해?"
    assert conversation.referenced_original_request == "산책 얼마나 시켜야 해?"


async def test_repeat_relation_and_its_referenced_turn_reach_the_answerer(
    monkeypatch,
) -> None:
    """수용 케이스 4 — REPEAT 판정과, 그것이 가리키는 앞선 거절이 실제로 answerer 의
    payload 까지 내려가야 실서비스(Gemini General)가 "같은 요구가 반복됐다" 를 읽고 같은
    고정 거절을 다시 안 낼 수 있다.

    브리프가 준 `response.message.strip() != prior.assistant.strip()` 비교는 이 하네스
    에서는 못 쓴다 — `RecordingAdapter` 는 관계와 무관하게 늘 고정 문자열
    (`"general 답"`) 만 내므로, resolver 를 통째로 지워도(REPEAT 이 NEW 가 돼도) 그 비교는
    항상 참이라 아무것도 못 잡는다. 그래서 실제 서비스가 REPEAT 을 다르게 답하는 데
    쓰는 재료 — `relation`과 `referenced_original_request` — 가 payload 까지 도달하는지로
    바꿔 쟀다: `conversation_context_of` 가 `relation` 을 그대로 안 옮기면 첫 assert 가,
    `referenced_original_request` 를 안 옮기면 두 번째가 죽는다.
    """
    monkeypatch.setattr(settings, "general_fallback", True)
    prior = _turn(
        "산책 후 발을 절뚝거려", "정확한 원인은 진단할 수 없어요. 동물병원에 방문해 보세요."
    )
    captured: dict = {}
    service = _service_with(
        resolver=_fixed(TurnRelation.REPEAT, referenced=prior),
        router_outputs=(json.dumps({"execute": [], "handoffs": []}),),
        general_sink=captured,
    )
    response = await service.run(
        query="그러니까 발을 저는 이유가 뭘 수 있는지 알고 싶다고",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        prior_turns=[prior],
    )
    assert response.status is not AssistantStatus.FAILED
    conversation = captured["payload"].conversation
    assert conversation.relation is TurnRelation.REPEAT
    assert conversation.referenced_original_request == prior.user


async def test_follow_up_after_a_clarification_carries_the_asked_axes(monkeypatch) -> None:
    """수용 케이스 5 — 되묻기 뒤 후속 답변이 원 질문에 붙고, 축은 '물은 것' 으로만 간다.

    `conversation_context_of` 의 앵커 판정(`pending is not None and resolved.
    pending_clarification_id == pending.turn_id`)을 지우고 무조건 채우거나 무조건 비우면
    이 assert 들이 죽는다 — `pending_question` 기본값은 `None`, `pending_missing_axes`
    기본값은 `[]` 라서 둘 다 정확한 값을 비교해야 트랩 2를 피한다(`_fixed_pending` 이
    `pending_clarification_id=pending.turn_id` 로 실제 앵커를 만든다)."""
    monkeypatch.setattr(settings, "general_fallback", True)
    pending = PendingClarification(
        turn_id=uuid.uuid4(),
        question="식욕과 활력 중 어느 쪽이 달라 보이나요?",
        missing_axes=[ObservationAxis.APPETITE, ObservationAxis.ENERGY],
    )
    captured: dict = {}
    service = _service_with(
        resolver=_fixed_pending(TurnRelation.FOLLOW_UP, pending),
        router_outputs=(json.dumps({"execute": [], "handoffs": []}),),
        general_sink=captured,
    )
    response = await service.run(
        query="밥은 먹는데 계속 누워 있어",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        pending_clarification=pending,
    )
    assert response.status is not AssistantStatus.FAILED
    conversation = captured["payload"].conversation
    assert conversation is not None
    assert conversation.pending_question == pending.question
    assert conversation.pending_missing_axes == [ObservationAxis.APPETITE, ObservationAxis.ENERGY]


async def test_meta_relation_reaches_the_answerer_instead_of_falling_through(
    monkeypatch,
) -> None:
    """수용 케이스 6 — 대화 자체에 대한 말("그니까 그걸 네가 나한테 물어봐야지")은
    off_topic 거절로 떨어지면 안 된다.

    `response.status is not AssistantStatus.REFUSED` 단독으로는 못 잰다 — 이 하네스의
    `RecordingAdapter` 는 관계와 무관하게 늘 `CapabilityStatus.OK` 를 내므로, resolver 를
    통째로 지워도(META 가 NEW 가 돼도) 그 비교는 항상 참이다(트랩 1, REPEAT 과 같은
    이유). 대신 META 관계와, 그것이 "무엇에 대한 되물음인지" 를 가리키는 앞선 turn 이
    실제로 answerer 의 payload 까지 도달하는지로 잰다 — 그래야 실서비스의 General
    프롬프트가 "질문에 대한 질문" 임을 읽고 off_topic 대신 답할 재료를 받는다.
    """
    monkeypatch.setattr(settings, "general_fallback", True)
    prior = _turn("오늘 건강 상태는 어때?", "증상의 원인이나 병명은 여기서 판단하지 않아요.")
    captured: dict = {}
    service = _service_with(
        resolver=_fixed(TurnRelation.META, referenced=prior),
        router_outputs=(json.dumps({"execute": [], "handoffs": []}),),
        general_sink=captured,
    )
    response = await service.run(
        query="그니까 그걸 네가 나한테 물어봐야지",
        principal=PRINCIPAL,
        context=dict(SEOUL),
        prior_turns=[prior],
    )
    assert response.status is not AssistantStatus.FAILED
    conversation = captured["payload"].conversation
    assert conversation.relation is TurnRelation.META
    assert conversation.referenced_original_request == prior.user
