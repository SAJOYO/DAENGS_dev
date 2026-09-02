"""대화 기록 HTTP 경계 — 서비스의 예외를 상태 코드로 바꿉니다.

판단은 여기 없습니다. "5개까지"·"어느 것을 밀어내나"·"두 번 눌렀을 때"는
`services/chat.py` 가 정합니다.

**경로가 `/app/chats` 인 이유**는 앱 회원 전용이기 때문입니다 (`/app/pets` 와 같은
규칙). 그리고 `CurrentAppUser` 를 씁니다 — `admin_or_app_user` 는 **principal 을
쓰지 않는** 엔드포인트 전용이고(core/deps.py 독스트링), 여기는 신원으로 남의 것을
걸러야 하는 API 라 관리자 토큰이 들어오면 `app_users` 에 없는 회원이 됩니다.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import SessionLocal, get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.models import ChatSession, ChatSummary, ChatTurn
from daengs_backend.schemas.chat import (
    ChatSessionCreate,
    ChatSessionDetailResponse,
    ChatSessionListResponse,
    ChatSessionResponse,
    ChatSummaryCreate,
    ChatSummaryListResponse,
    ChatSummaryResponse,
    ChatTurnResponse,
)
from daengs_backend.services import chat as chat_service
from daengs_backend.services.chat_summary import ChatSummaryError

router = APIRouter(prefix="/app/chats", tags=["chats"])

#: 문서에 같은 문장을 세 번 적지 않도록 모아 둡니다.
_NOT_MINE = {
    status.HTTP_404_NOT_FOUND: {
        "description": "내 대화(또는 내 강아지)가 아니거나 없습니다. "
        "**남의 것일 때도 404 입니다** — 403 으로 나누면 그 id 가 존재한다는 것을 알려 줍니다."
    }
}
_NEEDS_AUTH = {
    status.HTTP_401_UNAUTHORIZED: {"description": "앱 회원 인증이 필요합니다."}
}


def _session_response(chat_session: ChatSession) -> ChatSessionResponse:
    return ChatSessionResponse(
        id=chat_session.id,
        pet_id=chat_session.pet_id,
        title=chat_session.title,
        agent_categories=list(chat_session.agent_categories),
        created_at=chat_session.created_at,
        last_message_at=chat_session.last_message_at,
    )


def _turn_response(turn: ChatTurn) -> ChatTurnResponse:
    return ChatTurnResponse(
        id=turn.id,
        client_message_id=turn.client_message_id,
        processing_status=turn.processing_status,
        user_content=turn.user_content,
        assistant_content=turn.assistant_content,
        agent_categories=list(turn.agent_categories),
        assistant_status=turn.assistant_status,
        error_code=turn.error_code,
        completed_at=turn.completed_at,
        created_at=turn.created_at,
    )


def _summary_response(summary: ChatSummary) -> ChatSummaryResponse:
    return ChatSummaryResponse(
        id=summary.id,
        pet_id=summary.pet_id,
        source_session_id=summary.source_session_id,
        source_turn_count=summary.source_turn_count,
        title=summary.title,
        question_summary=summary.question_summary,
        answer_summary=summary.answer_summary,
        key_points=list(summary.key_points),
        cautions=list(summary.cautions),
        source_citations=list(summary.source_citations),
        agent_categories=list(summary.agent_categories),
        model=summary.model,
        prompt_version=summary.prompt_version,
        completed_at=summary.completed_at,
        created_at=summary.created_at,
    )


@router.get(
    "",
    response_model=ChatSessionListResponse,
    summary="최근 대화 목록 (내 계정 + 이 강아지, 최근 갱신 순)",
    responses={**_NEEDS_AUTH, **_NOT_MINE},
)
async def list_sessions(
    pet_id: uuid.UUID,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChatSessionListResponse:
    """최근 다섯 개까지. **다른 강아지의 대화는 섞이지 않습니다.**

    `max_sessions` 를 같이 보내는 이유는 앱이 "가장 오래된 것이 사라집니다" 를
    언제 보여 줄지 정하기 때문입니다. 앱에 숫자를 박아 두면 서버가 상한을 바꿀 때
    갈라집니다.
    """
    try:
        sessions = await chat_service.list_sessions(session, user.app_user_id, pet_id)
    except chat_service.PetNotOwnedError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다.") from None
    return ChatSessionListResponse(
        sessions=[_session_response(s) for s in sessions],
        max_sessions=chat_service.MAX_SESSIONS_PER_PET,
    )


@router.post(
    "",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="새 대화를 연다 (여섯 번째면 가장 오래된 것이 사라진다)",
    responses={**_NEEDS_AUTH, **_NOT_MINE},
)
async def create_session(
    body: ChatSessionCreate,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChatSessionResponse:
    """**여섯 번째를 만들면 그 강아지의 가장 오래된 대화가 사라집니다.**

    지우는 범위는 `내 계정 + 이 강아지` 한 쌍뿐입니다 — 다른 사용자의 것도, 같은
    사용자의 다른 강아지 것도 건드리지 않습니다. 저장해 둔 요약은 남습니다.
    """
    try:
        created = await chat_service.create_session(
            session, user.app_user_id, body.pet_id, body.title
        )
    except chat_service.PetNotOwnedError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다.") from None
    return _session_response(created)


@router.get(
    "/summaries",
    response_model=ChatSummaryListResponse,
    summary="보관함 — 저장된 AI 대화 요약 (원본이 사라진 것도 남는다)",
    responses={**_NEEDS_AUTH, **_NOT_MINE},
)
async def list_summaries(
    pet_id: uuid.UUID,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChatSummaryListResponse:
    """저장한 순서의 역순. **원본 대화가 5개 유지로 밀려난 요약도 그대로 나옵니다** —
    그때 `session_id` 만 `null` 이 됩니다.
    """
    try:
        summaries = await chat_service.list_summaries(session, user.app_user_id, pet_id)
    except chat_service.PetNotOwnedError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다.") from None
    return ChatSummaryListResponse(summaries=[_summary_response(s) for s in summaries])


# ⚠️ `/{session_id}` 보다 **먼저** 선언해야 합니다. FastAPI 는 등록 순서대로
# 매칭하므로 뒤에 두면 `/app/chats/summaries` 가 `get_session` 으로 가서
# "summaries" 를 UUID 로 파싱하려다 422 가 납니다 (`routers/pet.py` 의 `/primary`
# 와 같은 함정).
@router.get(
    "/{session_id}",
    response_model=ChatSessionDetailResponse,
    summary="대화 하나를 되살린다 (메시지 전부)",
    responses={**_NEEDS_AUTH, **_NOT_MINE},
)
async def get_session_detail(
    session_id: uuid.UUID,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChatSessionDetailResponse:
    """카드를 눌렀을 때. 메시지를 오간 순서대로 돌려줍니다."""
    try:
        chat_session, turns = await chat_service.get_session_with_turns(
            session, user.app_user_id, session_id
        )
    except chat_service.ChatSessionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "대화를 찾을 수 없습니다.") from None
    return ChatSessionDetailResponse(
        session=_session_response(chat_session),
        turns=[_turn_response(turn) for turn in turns],
    )


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="대화를 지운다 (저장된 요약은 남는다)",
    responses={**_NEEDS_AUTH, **_NOT_MINE},
)
async def delete_session(
    session_id: uuid.UUID,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """메시지도 같이 사라집니다. **보관함의 요약은 지워지지 않습니다.**"""
    try:
        await chat_service.delete_session(session, user.app_user_id, session_id)
    except chat_service.ChatSessionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "대화를 찾을 수 없습니다.") from None


@router.post(
    "/{session_id}/summary",
    response_model=ChatSummaryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="AI 요약 후 저장 — 이 대화만 요약해 보관함에 넣는다",
    responses={
        **_NEEDS_AUTH,
        **_NOT_MINE,
        status.HTTP_409_CONFLICT: {
            "description": "메시지가 없는 대화입니다. 빈 대화를 모델에 보내면 "
            "**없는 대화를 지어내므로** 부르기 전에 막습니다."
        },
        status.HTTP_502_BAD_GATEWAY: {
            "description": "요약 공급자가 실패했거나 출력이 스키마를 두 번 어겼습니다. "
            "**부분 저장을 하지 않습니다** — 빈 껍데기가 보관함에 남으면 저장에 성공한 것으로 보입니다."
        },
    },
)
async def create_summary(
    session_id: uuid.UUID,
    body: ChatSummaryCreate,
    user: CurrentAppUser,
) -> ChatSummaryResponse:
    """**사용자가 누를 때만 돕니다.** 답변마다 자동으로 다시 만들지 않습니다.

    같은 `client_request_id` 로 다시 부르면 이미 만든 요약을 그대로 돌려주고
    모델을 **아예 부르지 않습니다** — 두 번 눌렀거나 네트워크가 재시도한 것입니다.

    요약은 **이 대화만** 봅니다. 새 RAG 검색도, 새 상담도 하지 않고, 원문의
    주의·한계·출처를 그대로 보존합니다.
    """
    try:
        summary = await chat_service.create_summary(
            SessionLocal,
            user.app_user_id,
            session_id,
            client_request_id=body.client_request_id,
        )
    except chat_service.ChatSessionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "대화를 찾을 수 없습니다.") from None
    except chat_service.EmptyConversationError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "요약할 대화 내용이 없습니다."
        ) from None
    except chat_service.ExistingSummaryError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "SUMMARY_ALREADY_EXISTS", "summary_id": str(exc.summary_id)},
        ) from None
    except chat_service.SummaryProcessingError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "SUMMARY_PROCESSING", "summary_id": str(exc.summary_id)},
        ) from None
    except chat_service.SummaryRequestConflictError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "SUMMARY_REQUEST_ALREADY_FAILED"},
        ) from None
    except (chat_service.TurnLimitError, chat_service.TranscriptLimitError):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "SUMMARY_SOURCE_LIMIT_EXCEEDED"},
        ) from None
    except ChatSummaryError:
        # 원출력은 노출하지 않습니다 (O-14 와 같은 규칙).
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "요약을 만들지 못했습니다. 잠시 후 다시 시도해 주세요."
        ) from None
    return _summary_response(summary)


__all__ = ["router"]
