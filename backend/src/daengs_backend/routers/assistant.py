"""`POST /assistant/query` HTTP 경계.

판단은 여기 없다. 검증하고, 인증된 principal 로 `PrincipalContext` 를 만들고,
승인된 필드만으로 구조화 컨텍스트를 조립해 `Orchestrator` 를 부른다. 의미
라우팅·결정론적 RoutePlan 조립·능력 실행·집계는 전부 Card 2B/Card 1 의 것이다
(`orchestration/service.py` · `planner.py` · `semantic.py` · `graph.py`).

**어느 구현이 답하는지는 여기서 모른다.** `orchestration/runtime.py` 의
`build_orchestrator()` 가 고르고, 이 파일은 `run(...) -> AssistantResponse` 만
본다 — LangGraph 와 LangChain 에이전트를 갈아끼우는 자리가 그 한 곳인 이유다.

**대화 저장은 이 엔드포인트 하나로 들어온다** (D-048). 본문에 `chat_session_id` 와
`client_message_id` 가 함께 오면 같은 호출이 그 대화의 turn 으로 남고, 없으면 v0.0.0
그대로 무상태다. `/app/chats/{id}/turns` 같은 두 번째 실행 경로를 만들지 않는다 —
실행 경로가 둘이면 인증·라우팅·응답 계약이 둘이 된다. 저장의 규칙(예약 → 세션 닫기 →
호출 → 완료)은 `services/chat.py run_persisted_turn` 이 소유하고, 여기서는 그 예외를
상태 코드로 바꿀 뿐이다.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from daengs_backend.core.database import (
    get_chat_session_factory,
    get_metrics_session_factory,
)
from daengs_backend.core.deps import AppPrincipal, Perm, Principal, admin_or_app_user
from daengs_backend.orchestration.contracts import AssistantResponse, PrincipalContext
from daengs_backend.orchestration.runtime import Orchestrator, build_orchestrator
from daengs_backend.schemas.assistant import AssistantQueryRequest
from daengs_backend.services import chat as chat_service
from daengs_backend.services import dog_context as dog_context_service
from daengs_backend.services import request_metrics as metrics_service

router = APIRouter(tags=["assistant"])


def get_assistant_orchestration_service() -> Orchestrator:
    """어느 구현이 답할지는 `orchestration/runtime.py` 가 정합니다.

    여기서 `settings.orchestrator` 를 읽지 않는 이유: 이 함수는 **의존성 오버라이드
    지점**이라 테스트가 이미 갈아끼우고 있습니다. 선택 규칙까지 여기 두면 규칙이
    두 군데가 됩니다.
    """
    return build_orchestrator()


def _may_inspect_route(principal: Principal | AppPrincipal) -> bool:
    """`AssistantResponse.route` 를 이 사람에게 실을까 (#238).

    **인가 판단이라 여기서 합니다.** orchestration 은 `core.deps` 를 import 하지 않고,
    권한을 아는 층은 HTTP 경계뿐입니다. 앱 회원은 애초에 권한 목록이 비어 있어
    (`_principal_context`) 구조적으로 False 입니다 — 콘솔 점검 화면의 것이지 앱 기능이
    아니고, 저장되는 대화 turn 은 앱 회원 것뿐이라 `public_response_of` 로도 안 샙니다.

    **"항상 만들고 나중에 벗긴다" 를 하지 않는 이유**: `run_persisted_turn` 은 벗기기
    전에 응답을 적재합니다. 한 번 잊으면 DB 로 갑니다.
    """
    return isinstance(principal, Principal) and Perm.SEARCH_INSPECT in principal.permissions


def _principal_context(principal: Principal | AppPrincipal) -> PrincipalContext:
    """인증된 principal 에서만 만든다 — 요청 본문의 신원 필드는 절대 쓰지 않는다."""
    if isinstance(principal, Principal):
        return PrincipalContext(
            subject=str(principal.admin_id),
            kind="ADMIN",
            permissions=tuple(p.value for p in principal.permissions),
        )
    return PrincipalContext(subject=str(principal.app_user_id), kind="APP_USER")


def _structured_context(body: AssistantQueryRequest) -> dict[str, Any]:
    """승인된 필드만 명시적으로 담는다 — `body.model_dump()` 를 그대로 쓰지 않는다."""
    context: dict[str, Any] = {}
    if body.source is not None:
        context["source"] = body.source
    if body.action is not None:
        context["action"] = body.action
    if body.active_dog_id is not None:
        context["active_dog_id"] = body.active_dog_id
    if body.location is not None:
        context["location"] = {"lat": body.location.lat, "lon": body.location.lon}
    return context


async def _with_dog_context(
    context: dict[str, Any],
    principal: Principal | AppPrincipal,
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """`context["dog"]` 를 채워 돌려준다 — `context["location"]` 과 같은 자리다 (B4).

    payload 는 신뢰된 context 로만 조립된다(`planner.py`). 그래서 프로필 조회는 여기,
    DB 를 아는 층에서 하고 어댑터는 얇게 둔다 (D-035).

    **못 채워도 그냥 지나간다.** 관리자 토큰(pets 가 없다) · 활성 강아지 미지정 ·
    지워진 강아지 전부 여기로 온다. 프로필이 없다고 답할 수 있는 질문을 실패시키지 않는다 —
    B4 이전과 똑같은 답이 나갈 뿐이다.
    """
    active_dog_id = context.get("active_dog_id")
    if not isinstance(principal, AppPrincipal) or not isinstance(active_dog_id, str):
        return context
    async with session_factory() as session:
        dog = await dog_context_service.resolve(session, principal.app_user_id, active_dog_id)
    if dog is None:
        return context
    return {**context, "dog": dog}


@router.post(
    "/assistant/query",
    response_model=AssistantResponse,
    summary="자연어 질의 → 의미/결정론적 라우팅 → 능력 실행 (대화 저장은 선택)",
    responses={
        status.HTTP_403_FORBIDDEN: {
            "description": "`CHAT_PERSISTENCE_APP_USER_ONLY` — 관리자 토큰으로는 대화를 저장할 수 "
            "없습니다. 대화는 앱 회원의 것이라 관리자 `sub` 로는 소유권을 셀 수 없습니다."
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "내 대화가 아니거나 없습니다. **남의 것일 때도 404 입니다.**"
        },
        status.HTTP_409_CONFLICT: {
            "description": "`detail.code` 로 구분합니다 — "
            "`ACTIVE_DOG_MISMATCH` (`active_dog_id` 가 대화의 강아지와 다름, `session_pet_id` 동봉) · "
            "`CLIENT_MESSAGE_ID_REUSED` (같은 `client_message_id` 를 다른 질문에 재사용) · "
            "`TURN_PROCESSING` (같은 요청이 아직 답하는 중, 기다릴 것) · "
            "`TURN_FAILED` (그 `client_message_id` 는 실패로 끝남 — **새 UUID 로** 다시) · "
            "`TURN_LIMIT_EXCEEDED` (완료 turn 30개) · `TRANSCRIPT_LIMIT_EXCEEDED` (320,000자)."
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "본문 모양이 틀렸거나, 저장하는 요청의 질문이 2,000자를 넘습니다 "
            "(`QUESTION_TOO_LONG`). 무상태 요청에는 이 길이 제한이 없습니다."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "오케스트레이션 뒤 응답을 turn 으로 commit하지 못했습니다. "
            "`detail.code`는 `TURN_PERSISTENCE_FAILED`이고 `turn_id`, 내부 "
            "`persistence_error_code`, `retry_with_fresh_client_message_id: true`를 동봉합니다. "
            "생성된 답변 본문은 성공 응답으로 반환하지 않습니다."
        },
    },
)
async def query(
    body: AssistantQueryRequest,
    principal: Annotated[Principal | AppPrincipal, Depends(admin_or_app_user(Perm.READ))],
    service: Annotated[Orchestrator, Depends(get_assistant_orchestration_service)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_chat_session_factory)
    ],
    metrics_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_metrics_session_factory)
    ],
) -> AssistantResponse:
    """`AssistantResponse` 를 그대로 돌려준다. FAILED 를 포함해 상태를 재해석하지
    않는다 — 그것은 orchestration 계약이 소유한다 (docs/orchestration/contracts.md §5).

    `chat_session_id` + `client_message_id` 가 있으면 **같은 응답을 그 대화의 turn 으로
    남긴다.** 같은 두 값과 같은 질문을 다시 보내면 저장된 응답을 그대로 돌려주고 모델을
    부르지 않는다. 오케스트레이션이 실패하면 turn 은 실패로 닫히고 오류는 무상태일 때와
    똑같이 나간다.
    """
    # **지표는 곁다리다.** 못 남겨도 답변은 나간다 — 그 규칙은
    # `services/request_metrics.py` 머리말에 있고 여기서는 감싸기만 한다.
    #
    # `ignore=(HTTPException,)` 를 **여기서** 넘기는 것은, "무엇이 계약된 클라이언트
    # 오류인가"가 HTTP 경계의 일이기 때문이다 — 아래 `_dispatch` 의 `except` 열둘이
    # 전부 그것이고(없는 대화 · 중복 message id · 한도 초과), 그건 "오케스트레이션이
    # 어땠나" 가 아니라 "요청이 잘못 왔다" 이다. services 가 fastapi 를 알 이유도 없다.
    return await metrics_service.measured(
        metrics_factory,
        principal_kind=_principal_context(principal).kind,
        run=lambda: _dispatch(body, principal, service, session_factory),
        ignore=(HTTPException,),
    )


async def _dispatch(
    body: AssistantQueryRequest,
    principal: Principal | AppPrincipal,
    service: Orchestrator,
    session_factory: async_sessionmaker[AsyncSession],
) -> AssistantResponse:
    """실제 처리. `query` 에서 뽑아낸 것은 **지표를 재는 자리를 하나로 두려고**서다.

    나가는 길이 여럿이다 — 무상태 응답 하나, 저장 경로 하나, 그리고 계약된 오류 열두 갈래.
    각 자리에 계측을 붙이면 새 `except` 가 생길 때마다 빠뜨린다. 감싸면 한 곳이다.
    """
    principal_context = _principal_context(principal)
    include_route_trace = _may_inspect_route(principal)
    context = _structured_context(body)
    if not body.persists:
        return await service.run(
            query=body.query,
            principal=principal_context,
            context=await _with_dog_context(context, principal, session_factory),
            requested_capability=body.requested_capability,
            include_route_trace=include_route_trace,
        )

    if not isinstance(principal, AppPrincipal):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, {"code": "CHAT_PERSISTENCE_APP_USER_ONLY"}
        )
    assert body.chat_session_id is not None and body.client_message_id is not None

    async def orchestrate(active_dog_id: str) -> AssistantResponse:
        # 대화의 강아지가 힌트를 이긴다 — 서비스가 세션에서 읽은 pet_id 를 넘겨 준다.
        # 프로필 조회도 그 값으로 한다. 여기서 세션을 여는 것이 안전한 이유는
        # `run_persisted_turn` 이 예약 TX 를 닫고 부르기 때문이다 (그 docstring).
        return await service.run(
            query=body.query,
            principal=principal_context,
            context=await _with_dog_context(
                {**context, "active_dog_id": active_dog_id}, principal, session_factory
            ),
            requested_capability=body.requested_capability,
            # `include_route_trace` 를 여기서는 **안 넘깁니다.** 저장하는 요청은 바로 위에서
            # 앱 회원으로 좁혀져 있어 어차피 False 이고, 안 넘기는 쪽이 "저장되는 turn 에는
            # 라우팅 메타데이터가 실릴 수 없다"를 코드 모양으로 못박습니다 (#238).
        )

    try:
        return await chat_service.run_persisted_turn(
            session_factory,
            principal.app_user_id,
            session_id=body.chat_session_id,
            client_message_id=body.client_message_id,
            question=body.query,
            active_dog_id=body.active_dog_id,
            orchestrate=orchestrate,
        )
    except chat_service.AppUserNotActiveError:
        # `current_app_user` 와 같은 문장 — 앱이 재로그인으로 알아듣는 자리입니다.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "다시 로그인해 주세요.") from None
    except chat_service.ChatSessionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "대화를 찾을 수 없습니다.") from None
    except chat_service.ContentLimitError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"code": "QUESTION_TOO_LONG", "limit": exc.limit},
        ) from None
    except chat_service.ActiveDogMismatchError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "ACTIVE_DOG_MISMATCH", "session_pet_id": str(exc.session_pet_id)},
        ) from None
    except chat_service.TurnIdempotencyConflictError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "CLIENT_MESSAGE_ID_REUSED", "turn_id": str(exc.turn_id)},
        ) from None
    except chat_service.TurnProcessingError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "TURN_PROCESSING", "turn_id": str(exc.turn_id)},
        ) from None
    except chat_service.TurnFailedError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "TURN_FAILED", "turn_id": str(exc.turn_id), "error_code": exc.error_code},
        ) from None
    except chat_service.TurnLimitError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "TURN_LIMIT_EXCEEDED", "limit": chat_service.MAX_COMPLETED_TURNS},
        ) from None
    except chat_service.TranscriptLimitError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "TRANSCRIPT_LIMIT_EXCEEDED", "limit": chat_service.MAX_TRANSCRIPT_CHARS},
        ) from None
    except chat_service.TurnPersistenceError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {
                "code": "TURN_PERSISTENCE_FAILED",
                "turn_id": str(exc.turn_id),
                "persistence_error_code": exc.persistence_error_code,
                "retry_with_fresh_client_message_id": (
                    exc.retry_with_fresh_client_message_id
                ),
            },
        ) from None


# `get_chat_session_factory` 는 core/database.py 의 것을 그대로 내보낸다 — 요약 라우터와
# 같은 의존성이라 테스트가 한 번 바꾸면 두 라우터가 같이 계측된다. 무상태 요청은 이것을
# 한 번도 부르지 않는다.
#
# **지표는 그것과 다른 공장을 쓴다** (`get_metrics_session_factory`). 같은 `SessionLocal`
# 을 돌려주지만 의존성이 갈려 있어야 테스트가 "대화를 몇 번 열었나" 와 "지표를 남겼나"
# 를 따로 볼 수 있다. 그리고 지표는 **무상태 요청도 남긴다** — 위 주석의 "무상태 요청은
# 한 번도 부르지 않는다" 는 대화 공장 이야기다 (#297).
__all__ = ["get_chat_session_factory", "get_metrics_session_factory", "router"]
