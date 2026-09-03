"""`POST /assistant/query` HTTP 경계.

판단은 여기 없다. 검증하고, 인증된 principal 로 `PrincipalContext` 를 만들고,
승인된 필드만으로 구조화 컨텍스트를 조립해 `AssistantOrchestrationService` 를
부른다. 의미 라우팅·결정론적 RoutePlan 조립·능력 실행·집계는 전부 Card 2B/Card 1
의 것이다 (`orchestration/service.py` · `planner.py` · `semantic.py` · `graph.py`).

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

from daengs_backend.core.database import get_chat_session_factory
from daengs_backend.core.deps import AppPrincipal, Perm, Principal, admin_or_app_user
from daengs_backend.orchestration.contracts import AssistantResponse, PrincipalContext
from daengs_backend.orchestration.service import AssistantOrchestrationService
from daengs_backend.schemas.assistant import AssistantQueryRequest
from daengs_backend.services import chat as chat_service

router = APIRouter(tags=["assistant"])


def get_assistant_orchestration_service() -> AssistantOrchestrationService:
    return AssistantOrchestrationService()


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
    service: Annotated[AssistantOrchestrationService, Depends(get_assistant_orchestration_service)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_chat_session_factory)
    ],
) -> AssistantResponse:
    """`AssistantResponse` 를 그대로 돌려준다. FAILED 를 포함해 상태를 재해석하지
    않는다 — 그것은 orchestration 계약이 소유한다 (orchestration-contracts.md §5).

    `chat_session_id` + `client_message_id` 가 있으면 **같은 응답을 그 대화의 turn 으로
    남긴다.** 같은 두 값과 같은 질문을 다시 보내면 저장된 응답을 그대로 돌려주고 모델을
    부르지 않는다. 오케스트레이션이 실패하면 turn 은 실패로 닫히고 오류는 무상태일 때와
    똑같이 나간다.
    """
    principal_context = _principal_context(principal)
    context = _structured_context(body)
    if not body.persists:
        return await service.run(
            query=body.query,
            principal=principal_context,
            context=context,
            requested_capability=body.requested_capability,
        )

    if not isinstance(principal, AppPrincipal):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, {"code": "CHAT_PERSISTENCE_APP_USER_ONLY"}
        )
    assert body.chat_session_id is not None and body.client_message_id is not None

    async def orchestrate(active_dog_id: str) -> AssistantResponse:
        # 대화의 강아지가 힌트를 이긴다 — 서비스가 세션에서 읽은 pet_id 를 넘겨 준다.
        return await service.run(
            query=body.query,
            principal=principal_context,
            context={**context, "active_dog_id": active_dog_id},
            requested_capability=body.requested_capability,
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
__all__ = ["get_chat_session_factory", "router"]
