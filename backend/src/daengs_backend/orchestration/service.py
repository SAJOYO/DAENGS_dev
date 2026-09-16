"""Thin planning layer in front of the existing Card 1 execution core.

Deterministic signal first, semantic Gemini selection only as the fallback, then
the plan goes to the untouched OrchestrationEngine. A router/system failure
(O-14) executes nothing and returns top-level FAILED — it is never CLARIFY, and
the invalid model output is never surfaced. A purely social utterance
(greeting/thanks/goodbye, classified by the router with no capability intent) is
answered by a fixed template before any RoutePlan exists, so it never reaches
the engine. `/assistant/query` itself is Card 3; this module deliberately
registers no endpoint.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from daengs_backend.config import settings
from daengs_backend.core.tracing import request_trace
from daengs_backend.orchestration.care_log import (
    build_care_log_declined_response,
    confirmation_of,
)
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    PrincipalContext,
    RoutePlan,
    RouterKind,
    RouteTrace,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.planner import (
    assemble_route_plan,
    resolve_care_log_route,
    resolve_care_log_write,
    resolve_deterministic_route,
    resolve_emergency_route,
    resolve_gait_route,
    resolve_skin_route,
)
from daengs_backend.orchestration.resolver import (
    RESOLUTION_CONFIDENCE_FLOOR,
    GeminiTurnResolver,
    PendingClarification,
    PriorTurn,
    ResolvedTurn,
    TurnRelation,
    TurnResolutionError,
    conversation_context_of,
)
from daengs_backend.orchestration.semantic import (
    PROMPT_VERSION,
    ROUTER_MODEL_ID,
    GeminiSemanticRouter,
    SemanticRoutingError,
    router_prompt_version,
)
from daengs_backend.orchestration.social import build_social_response

_ROUTER_FAILURE_MESSAGE = "요청을 해석하지 못했습니다. 잠시 후 다시 시도해 주세요."
LOGGER = logging.getLogger(__name__)
_KST = ZoneInfo("Asia/Seoul")
#: 야간의 경계. 문구만 가르고 순위는 안 바꾸므로 정밀할 필요가 없다 —
#: 야간 순위 부스트는 `24h` 태그 실측 뒤의 별도 카드다.
_NIGHT_FROM_HOUR = 20
_NIGHT_UNTIL_HOUR = 8


def _is_night(now: datetime) -> bool:
    hour = now.astimezone(_KST).hour
    return hour >= _NIGHT_FROM_HOUR or hour < _NIGHT_UNTIL_HOUR


class AssistantOrchestrationService:
    def __init__(
        self,
        *,
        engine: OrchestrationEngine | None = None,
        semantic_router: GeminiSemanticRouter | None = None,
        turn_resolver: GeminiTurnResolver | None = None,
    ) -> None:
        self._engine = engine or OrchestrationEngine()
        self._semantic_router = semantic_router or GeminiSemanticRouter()
        self._turn_resolver = turn_resolver or GeminiTurnResolver()

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
    ) -> AssistantResponse:
        """Plan, then execute. `include_route_trace` is the caller's answer to "may this
        principal see how the request was routed?" (#238) — the HTTP boundary decides it,
        because permissions are its business, and it defaults to no.

        `prior_turns`/`pending_clarification` (#416 Task 5) feed the Turn Resolver — both
        default to empty/None so every existing caller (including the HTTP router, which
        does not thread conversation history yet) keeps behaving exactly as before: no
        candidates and no pending clarification means the resolver's fast path returns a
        `NEW` turn without calling any model.

        The two responses that never reach the engine get the same trace attached here:
        a social reply and a router failure are exactly the answers whose "no capability
        ran" is otherwise unexplained in the console.
        """
        rid = request_id or str(uuid.uuid4())
        structured_context = dict(context or {})
        # 요청의 루트 런. 시맨틱 라우터의 LLM 런과 그래프(자식 `orchestration_engine`)가
        # 전부 이 아래에 붙는다 — 라우터가 그래프보다 먼저 돌아서, 루트를 그래프에
        # 두면 라우터 런이 어느 트레이스에도 못 붙는다 (`core.tracing` 모듈 docstring).
        # 트레이싱이 꺼져 있으면 이 컨텍스트는 아무것도 안 보낸다.
        async with request_trace(
            request_id=rid,
            run_name="assistant_query",
            inputs={
                "query": query,
                "requested_capability": requested_capability,
                "context": structured_context,
                "locale": locale,
            },
            metadata={"principal_kind": principal.kind, "locale": locale},
        ) as run:
            response, route_plan, resolved_router_version = await self._plan_and_execute(
                query=query,
                principal=principal,
                structured_context=structured_context,
                requested_capability=requested_capability,
                rid=rid,
                locale=locale,
                include_route_trace=include_route_trace,
                prior_turns=prior_turns,
                pending_clarification=pending_clarification,
            )
            # 라우팅 종류는 돌고 나서야 안다. 자식(그래프)의 metadata 와 같은 키다 —
            # 루트 실행을 같은 쿼리로 거를 수 있게 하는 계약이다.
            run.add_metadata(
                _route_metadata(
                    route_plan,
                    resolved_router_version=resolved_router_version,
                    # 케어 기록 거절은 RoutePlan 없이 **결정론으로** 끝난다 (D-075). 그것을
                    # 가리는 값이 `resolved_router_version is None` 이다 — 시맨틱 라우터를
                    # 거친 두 응답(스몰토크·라우터 실패)은 그 자리에서 버전을 이미 계산해
                    # 두므로 None 이 아니다.
                    fallback_router=(
                        RouterKind.DETERMINISTIC
                        if resolved_router_version is None
                        else RouterKind.LLM
                    ),
                )
            )
            run.end(outputs=_trace_outputs(response))
            return response

    async def _plan_and_execute(
        self,
        *,
        query: str,
        principal: PrincipalContext,
        structured_context: dict[str, Any],
        requested_capability: str | None,
        rid: str,
        locale: str,
        include_route_trace: bool,
        prior_turns: Sequence[PriorTurn] = (),
        pending_clarification: PendingClarification | None = None,
    ) -> tuple[AssistantResponse, RoutePlan | None, str | None]:
        """계획하고 실행한다. 둘째 반환값은 트레이스 metadata 용 — 엔진에 못 간 두 응답
        (스몰토크 · 라우터 실패)은 RoutePlan 이 없어서 None 이다. 셋째 반환값
        (`resolved_router_version`)도 metadata 용이다 — 실제로 나간 라우터 프롬프트
        버전을 `_route_metadata` 가 `AssistantResponse.route` 를 거치지 않고 바로 읽게
        한다. `route` 는 `include_route_trace=False` 인 영속 경로에서 늘 `None` 이라,
        그것을 거치면 권한 결정 하나로 측정 핀이 틀어진다 (fix wave item 5)."""
        # 응급은 라우터보다 앞이다 — 모델을 태우지 않고, 배타로 끝낸다.
        # 시각은 한 번만 읽는다 — 응급의 야간 판정과 케어 기록의 `occurred_at` 이 같은
        # 요청 안에서 갈리면, 자정 경계에서 두 판정이 서로 다른 하루를 보게 된다.
        now_kst = datetime.now(tz=_KST)
        route_plan = resolve_emergency_route(
            query=query,
            context=structured_context,
            requested_capability=requested_capability,
            at_night=_is_night(now_kst),
        )
        # ── 피부 판정 해설 (D-079). 응급 **뒤**, 명시 신호 **앞**.
        # 같은 `skin` 신호를 `resolve_deterministic_route` 는 HANDOFF 로 읽는다 — 서버가 해소한
        # 판정 기록이 붙은 요청만 여기서 먼저 가로챈다. 응급이 앞인 이유는 다른 게이트와 같다:
        # "피부가 벌겋고 숨을 헐떡여요" 는 해설이 아니라 병원이다.
        if route_plan is None:
            route_plan = resolve_skin_route(
                query=query,
                context=structured_context,
                requested_capability=requested_capability,
                enabled=settings.skin_agent,
            )
        # ── 보행 변화 관찰 해설 (D-080). 피부와 같은 자리, 같은 이유.
        # `gait` 신호도 `resolve_deterministic_route` 가 HANDOFF 로 읽는다 — 서버가 소유를
        # 확인하고 계산한 비교(또는 못 한 이유)가 붙은 요청만 여기서 먼저 가로챈다.
        # 응급이 앞인 이유도 같다: "다리를 아예 못 디뎌요" 는 변화 관찰이 아니라 병원이다.
        # skin 과 서로 순서가 무관한 것은 신호 이름이 달라 둘이 겹칠 수 없어서다.
        if route_plan is None:
            route_plan = resolve_gait_route(
                query=query,
                context=structured_context,
                requested_capability=requested_capability,
                enabled=settings.gait_agent,
            )
        if route_plan is None:
            route_plan = resolve_deterministic_route(
                requested_capability=requested_capability,
                query=query,
                context=structured_context,
            )
        # ── 케어 기록 (#331 후속, D-075). 응급·명시 신호 **뒤**, Turn Resolver·라우터 **앞**.
        #
        # 응급이 앞인 이유는 `resolve_emergency_route` 와 같다 — `"밥 먹였는데 토해요"` 는
        # 기록 의도가 아니라 응급이고, 그 경계를 이 게이트가 약하게 만들 수 없다. 명시 신호가
        # 앞인 이유도 리졸버와 같다: 신호가 이미 답이라 물을 것이 없다.
        #
        # **모델보다 앞인 것이 이 배치의 핵심이다.** 쓰기 경로에 모델 호출이 0회인 것이
        # 여기서 성립하고(`care_log.py` 머리말), 승낙 한 마디(`"네"`)에 리졸버 왕복을
        # 태우지 않는 것도 부수적으로 따라온다.
        if route_plan is None:
            pending_proposal = (
                pending_clarification.care_log if pending_clarification is not None else None
            )
            route_plan = resolve_care_log_write(
                query=query, pending=pending_proposal, now=now_kst
            )
            # 승낙이 아니었다. 거절은 고정 문구로 끝내고, 그 밖의 발화는 제안을 흘려
            # 평소대로 라우팅한다 — 대기 되묻기를 안 이어받는 기존 동작과 같다.
            if (
                route_plan is None
                and pending_proposal is not None
                and confirmation_of(query) == "decline"
            ):
                return build_care_log_declined_response(request_id=rid), None, None
        if route_plan is None:
            route_plan = resolve_care_log_route(
                query=query,
                context=structured_context,
                now=now_kst,
                # 읽는 자리가 요청 시점인 것은 `general_fallback`·`turn_resolver` 와 같은
                # 이유다 — 테스트가 켜고 끌 수 있어야 하고, 서버는 `.env` 한 줄로 켠다.
                care_log_write=settings.care_log_write,
            )
        # ── Turn Resolver (#416). 응급·결정론 **뒤**, 시맨틱 라우터 **앞**.
        # 응급이 앞인 것은 의도다: 응급 경계는 현재 사용자 원문을 직접 검사해야 하고, 이
        # 판정이 그것을 약하게 만들 수 없다. 결정론이 앞인 것은 명시 신호가 이미 답이기
        # 때문이다 — 관계를 물을 이유가 없다.
        resolved: ResolvedTurn | None = None
        # 읽는 자리가 여기(요청 시점)인 것은 의도다 — 모듈 최상단에서 읽으면 테스트가
        # 플래그를 켜고 끌 수 없고, 서버는 `.env` 한 줄로 켜고 재시작한다 (#279 와 같은
        # 이유, `general_fallback` 이 아래에서 읽히는 자리와 같은 규칙).
        if route_plan is None and settings.turn_resolver:
            try:
                resolved = await self._turn_resolver.resolve(
                    query=query, candidates=prior_turns, pending=pending_clarification
                )
            except TurnResolutionError as exc:
                # **답은 나간다.** 이력 기제가 없던 때와 같게 도는 것이 실패 모드다 —
                # 대화 이어짐이 안 되는 것이 답이 안 나오는 것보다 낫다. 질문 원문은 안
                # 남긴다 (D-037) — request_id 로 트레이스와 잇는다. 시맨틱 라우터 실패
                # 로그(아래)와 같은 규칙: 담는 것과 안 담는 것이 같다.
                LOGGER.warning(
                    "턴 해소 실패 request_id=%s: %s (원인: %r)",
                    rid,
                    exc,
                    exc.__cause__,
                )
                resolved = None
            else:
                if (
                    resolved.relation is TurnRelation.NEW
                    or resolved.resolution_confidence < RESOLUTION_CONFIDENCE_FLOOR
                ):
                    # **CLARIFY 생산자를 셋으로 안 만든다.** 확신이 낮으면 붙임을 버리기만
                    # 한다 — General 의 기존 ask 경로가 오늘 하던 대로 되묻는다. 넘길 것이
                    # 없으면 넘기지 않는다: 바이트 동일 보장.
                    resolved = None
        # **변환은 여기, 딱 한 번** (R14). `ResolvedTurn` 과 `PendingClarification` 을 둘 다
        # 들고 있는 것은 이 계층뿐이다 — 시맨틱 라우터와 `assemble_route_plan` 양쪽에 같은
        # `ConversationContext` 객체를 넘겨서, 한쪽만 `pending_question` 을 채우는 식의
        # 드리프트가 애초에 생길 수 없게 한다.
        conversation = conversation_context_of(resolved, pending_clarification)
        # `route_plan` 이 이미 채워져 있으면(응급·결정론) 시맨틱 라우터를 아예 안 거치므로
        # 프롬프트 버전을 계산할 것이 없다 — `_route_metadata` 도 그 경우 `route_plan`
        # 가지에서 `route_plan.prompt_version` 을 직접 읽어서 이 `None` 을 안 쓴다.
        resolved_router_version: str | None = None
        if route_plan is None:
            # `conversation` 이 여기서야 자리를 잡으므로, 실제로 나갈 라우터 프롬프트의
            # 버전도 여기서 한 번만 계산한다(Fix round 1, R18) — `RouteTrace` ·
            # `RoutePlan.prompt_version` · 아래 `_route_metadata` 의 폴백 세 곳이 전부 이
            # 값을 읽어서, 실제로 `RESOLVED_PROMPT_VERSION` 프롬프트가 나간 turn 이 평가
            # 랩 행에 평범한 `PROMPT_VERSION` 으로 잘못 적히는 일이 다시 생기지 않는다.
            resolved_router_version = router_prompt_version(
                conversation, facility_view=structured_context.get("facility_view") is True
            )
            semantic_trace = (
                RouteTrace(
                    router=RouterKind.LLM,
                    model=ROUTER_MODEL_ID,
                    prompt_version=resolved_router_version,
                )
                if include_route_trace
                else None
            )
            try:
                decision = await self._semantic_router.select(
                    query=query,
                    context=structured_context,
                    resolved=conversation,
                )
            except SemanticRoutingError as exc:
                # 사용자에게는 고정 문구만 나가고 모델의 잘못된 출력은 안 보인다 (O-14).
                # 그래서 **여기가 원인이 남는 유일한 자리**다 — 프로바이더 예외(한도 초과 ·
                # 타임아웃)와 스키마 실패가 같은 FAILED 로 나가는데, 로그가 없으면 둘을
                # 못 가른다 (2026-09-07 개발 PC 에서 그 상태로 두 번 헛돌았다). 질문 원문은
                # 안 남긴다 (D-037) — request_id 로 트레이스와 잇는다.
                LOGGER.warning(
                    "시맨틱 라우터 실패 request_id=%s: %s (원인: %r)",
                    rid,
                    exc,
                    exc.__cause__,
                )
                return (
                    AssistantResponse(
                        request_id=rid,
                        status=AssistantStatus.FAILED,
                        message=_ROUTER_FAILURE_MESSAGE,
                        results=[],
                        handoffs=[],
                        clarify=None,
                        route=semantic_trace,
                    ),
                    None,
                    resolved_router_version,
                )
            if decision.social_intent is not None:
                # Schema guarantees execute/handoffs are empty here: nothing to plan or run.
                return (
                    build_social_response(
                        request_id=rid, intent=decision.social_intent, route=semantic_trace
                    ),
                    None,
                    resolved_router_version,
                )
            route_plan = assemble_route_plan(
                decision,
                query=query,
                context=structured_context,
                router=RouterKind.LLM,
                model=ROUTER_MODEL_ID,
                prompt_version=resolved_router_version,
                # 읽는 자리가 여기(요청 시점)인 것은 의도다 — 모듈 최상단에서 읽으면 테스트가
                # 플래그를 켜고 끌 수 없고, 서버는 `.env` 한 줄로 켜고 재시작한다 (#279).
                general_fallback=settings.general_fallback,
                resolved=conversation,
            )
        response = await self._engine.run(
            route_plan=route_plan,
            query=query,
            principal=principal,
            request_id=rid,
            locale=locale,
            context=structured_context,
            include_route_trace=include_route_trace,
        )
        return response, route_plan, resolved_router_version


def _route_metadata(
    route_plan: RoutePlan | None,
    *,
    resolved_router_version: str | None,
    fallback_router: RouterKind = RouterKind.LLM,
) -> dict[str, Any]:
    """루트 런 metadata 의 라우팅 키. `graph.py` 의 자식 런과 **같은 키**를 쓴다.

    RoutePlan 이 없는 두 응답(스몰토크 · 라우터 실패)은 시맨틱 라우터를 거친 뒤라
    LLM 라우팅으로 적는다 — 그래서 `fallback_router` 의 기본값이 LLM 이다.

    **기본값이 아닌 경우가 하나 생겼다** (#331 후속, D-075). 케어 기록 **거절**
    (`"아니 됐어"`)은 결정론 게이트가 모델을 한 번도 안 태우고 고정 문구로 끝내므로,
    RoutePlan 없이 끝나면서도 LLM 라우팅이 아니다 — 부르는 쪽이
    `fallback_router=RouterKind.DETERMINISTIC` 를 넘긴다. 예전 주석은 "결정론 라우팅은
    RoutePlan 없이 끝나는 길이 없다" 였는데, 그 문장은 이제 사실이 아니다. 여기서 LLM 으로
    적으면 **돌지 않은 모델 호출이 평가 랩 행에 한 건 생긴다.**

    `resolved_router_version` 은 그 두 응답을 낳은 `_plan_and_execute` 가 그 자리에서 이미
    실제로 쓰인 `prompt_version` 을 계산해 둔 값이다(Fix round 1, R18) — 이 함수는 그것을
    `AssistantResponse.route`(`RouteTrace`) 를 거치지 않고 직접 받는다(fix wave item 5).
    예전에는 `route` 를 거쳐 갔는데, `route` 는 `include_route_trace=False` 인 영속 경로
    에서 늘 `None` 이라 그 경로에서는 이 값이 절대 안 실렸다 — 권한 결정 하나가 측정용
    프롬프트 버전 핀을 틀어지게 만든 것이다. 여기서 `PROMPT_VERSION` 을 다시 하드코딩하면
    맥락이 실린 turn 도 평범한 버전으로 적힌다. `resolved_router_version` 이 `None` 인
    것은 시맨틱 라우터를 거치지 않은 경우뿐이고(이 가지에 들어오는 두 응답은 둘 다 거치므로
    실무에서는 안 생긴다), 그때는 기본값으로 물러난다.
    """
    if route_plan is None:
        if fallback_router is RouterKind.DETERMINISTIC:
            # 모델이 안 돌았으니 모델 이름도 프롬프트 버전도 없다 — `RoutePlan` 의 결정론
            # 경로가 둘을 None 으로 두는 것과 같은 뜻이다 ("모른다" 가 아니라 "없었다").
            return {
                "router": RouterKind.DETERMINISTIC.value,
                "router_model": None,
                "prompt_version": None,
            }
        return {
            "router": RouterKind.LLM.value,
            "router_model": ROUTER_MODEL_ID,
            "prompt_version": resolved_router_version or PROMPT_VERSION,
        }
    return {
        "router": route_plan.router.value,
        "router_model": route_plan.model,
        "prompt_version": route_plan.prompt_version,
    }


def _trace_outputs(response: AssistantResponse) -> dict[str, Any]:
    """루트 런 출력. **상태만** 싣는다 — 목록에서 실패·명확화를 바로 거르기 위한 것.

    답 본문·근거 청크는 자식 런(그래프 최종 상태 · `training_rag`)에 이미 있어서
    여기 또 실으면 같은 텍스트가 한 요청에 두 번 나간다.

    맨 앞의 가드 이유는 `daengs_training/service.py` `_trace_outputs` 와 같다.
    """
    from langsmith import utils as ls_utils

    if not ls_utils.tracing_is_enabled():
        return {}
    return {
        "status": response.status.value,
        "results": [
            {"capability": result.capability.value, "status": result.status.value}
            for result in response.results
        ],
        "handoffs": [handoff.target for handoff in response.handoffs],
        "clarify": response.clarify is not None,
    }


__all__ = ["AssistantOrchestrationService"]
