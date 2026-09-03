"""대화 기록 HTTP 경계 — 서비스의 예외를 상태 코드로 바꿉니다.

판단은 여기 없습니다. "5개까지"·"어느 것을 밀어내나"·"두 번 눌렀을 때"는
`services/chat.py` 가 정합니다.

**경로가 `/app/chats` 인 이유**는 앱 회원 전용이기 때문입니다 (`/app/pets` 와 같은
규칙). 그리고 `CurrentAppUser` 를 씁니다 — `admin_or_app_user` 는 **principal 을
쓰지 않는** 엔드포인트 전용이고(core/deps.py 독스트링), 여기는 신원으로 남의 것을
걸러야 하는 API 라 관리자 토큰이 들어오면 `app_users` 에 없는 회원이 됩니다.

**예외가 하나 있습니다 — `POST /{session_id}/summary` 는 `CurrentAppMemberTokenOnly`
입니다.** 그 엔드포인트만 요청 수명 세션이 없고, 서비스가 짧은 TX 를 따로 여닫습니다
(예약 → 세션 닫기 → Gemini → 완료). `CurrentAppUser` 가 요청 세션에서 잡은
`app_users FOR UPDATE` 를 서비스의 두 번째 TX 가 INSERT 의 FK 로 다시 기다리면
자기 교착이 됩니다 — 2026-09-03 서버 Phase 3A 에서 실제로 워커가 영영 멈췄습니다.
active 확인은 서비스의 예약 TX 가 같은 잠금으로 다시 합니다 (`services/chat.py`).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from daengs_backend.core.database import get_chat_session_factory, get_session
from daengs_backend.core.deps import CurrentAppMemberTokenOnly, CurrentAppUser
from daengs_backend.models import ChatSession, ChatSummary, ChatTurn
from daengs_backend.orchestration.contracts import AssistantResponse
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
from daengs_backend.services.chat_summary import ChatSummaryError, GeminiChatSummarizer

router = APIRouter(prefix="/app/chats", tags=["chats"])


def get_chat_summarizer() -> GeminiChatSummarizer:
    """요약 공급자. 테스트는 이 의존성을 가짜 `generate` 를 가진 것으로 바꿉니다."""
    return GeminiChatSummarizer()


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
        # 저장된 JSON 을 공개 계약으로 다시 검증해서 내보냅니다 — 모양이 어긋난 행이 있으면
        # 여기서 드러나야지, 앱이 알 수 없는 키를 받아서는 안 됩니다.
        public_response=(
            AssistantResponse.model_validate(turn.public_response)
            if turn.public_response is not None
            else None
        ),
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
    summary="새 대화 초안을 연다 (첫 답변이 전달되면 최근 대화로 활성화)",
    responses={**_NEEDS_AUTH, **_NOT_MINE},
)
async def create_session(
    body: ChatSessionCreate,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChatSessionResponse:
    """초안은 최근 대화 다섯 개에 포함되지 않습니다.

    첫 답변이 사용자에게 전달되어 활성 대화가 여섯 개가 되는 순간, `내 계정 + 이
    강아지` 범위의 가장 오래된 활성 대화가 사라집니다. 다른 사용자의 것도, 같은
    사용자의 다른 강아지 것도 건드리지 않고, 저장해 둔 요약은 남습니다.
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


@router.delete(
    "/summaries/{summary_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="보관함에서 요약 하나를 지운다 (원본 대화는 그대로)",
    responses={
        **_NEEDS_AUTH,
        **_NOT_MINE,
        status.HTTP_409_CONFLICT: {
            "description": "아직 만드는 중인 요약입니다 (`SUMMARY_PROCESSING`). 끝나기 전에는 "
            "지울 수 없습니다 — 지우면 완료 쓰기가 갈 곳을 잃고 사용자는 502 를 봅니다."
        },
    },
)
async def delete_summary(
    summary_id: uuid.UUID,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """**완성된 요약만** 지웁니다. 만드는 데 실패한 예약은 보관함에 없으므로 404 입니다."""
    try:
        await chat_service.delete_summary(session, user.app_user_id, summary_id)
    except chat_service.ChatSummaryNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "요약을 찾을 수 없습니다.") from None
    except chat_service.SummaryProcessingError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "SUMMARY_PROCESSING", "summary_id": str(exc.summary_id)},
        ) from None


# ⚠️ `/summaries...` 는 `/{session_id}` 보다 **먼저** 선언해야 합니다. FastAPI 는 등록
# 순서대로 매칭하므로 뒤에 두면 `/app/chats/summaries` 가 `get_session` 으로 가서
# "summaries" 를 UUID 로 파싱하려다 422 가 납니다 (`routers/pet.py` 의 `/primary`
# 와 같은 함정). `tests/test_chat_api.py` 가 이 순서를 지킵니다.
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
            "description": "모델을 부르기 전에 막는 경우들. `detail.code` 로 구분합니다 — "
            "`SUMMARY_ALREADY_EXISTS` (같은 원본 상태를 이미 요약함, `summary_id` 동봉) · "
            "`SUMMARY_PROCESSING` (같은 요약을 만드는 중, `summary_id` 동봉) · "
            "`SUMMARY_REQUEST_ALREADY_FAILED` (같은 `client_request_id` 가 실패로 끝남, 새 id 로) · "
            "`SUMMARY_SOURCE_LIMIT_EXCEEDED` (turn 30개·transcript 320,000자 초과). "
            "메시지가 없는 대화도 409 입니다 — 빈 대화를 모델에 보내면 **없는 대화를 지어내므로** "
            "부르기 전에 막습니다."
        },
        status.HTTP_502_BAD_GATEWAY: {
            "description": "요약 공급자가 실패했거나 출력이 스키마를 두 번 어겼습니다. "
            "**부분 저장을 하지 않습니다** — 빈 껍데기가 보관함에 남으면 저장에 성공한 것으로 보입니다."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "요약은 만들어졌지만 completed 행으로 commit하지 못했습니다 — 회원은 "
            "active 인데 예약이 이미 `processing` 이 아닙니다(5분 stale 회수 등). 탈퇴는 여기가 "
            "아니라 완료 단계의 active 확인에서 401 로 끝납니다. `detail.code`는 "
            "`SUMMARY_PERSISTENCE_FAILED`이고 `summary_id`, 내부 `persistence_error_code`, "
            "`retry_with_fresh_client_request_id: true`를 동봉합니다. **생성된 요약 본문은 "
            "성공 응답으로 반환하지 않고, 사라진 행을 다시 만들지도 않습니다** — "
            "`/assistant/query`의 `TURN_PERSISTENCE_FAILED`와 같은 계약입니다."
        },
    },
)
async def create_summary(
    session_id: uuid.UUID,
    body: ChatSummaryCreate,
    user: CurrentAppMemberTokenOnly,
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_chat_session_factory)
    ],
    summarizer: Annotated[GeminiChatSummarizer, Depends(get_chat_summarizer)],
) -> ChatSummaryResponse:
    """**사용자가 누를 때만 돕니다.** 답변마다 자동으로 다시 만들지 않습니다.

    같은 원본 상태를 이미 요약했거나 같은 `client_request_id`를 다시 보내면 409와
    기존 요약 ID 또는 처리 상태를 돌려주고 모델을 **아예 부르지 않습니다**.

    요약은 **이 대화만** 봅니다. 새 RAG 검색도, 새 상담도 하지 않고, 원문의
    주의·한계·출처를 그대로 보존합니다.

    인증은 **토큰만** 봅니다 (`CurrentAppMemberTokenOnly`) — 요청 수명 DB 세션이 없습니다.
    회원이 아직 active 인지는 서비스가 예약 TX 안에서 같은 잠금으로 다시 확인하고,
    아니면 `current_app_user` 와 같은 401 입니다. 모델이 도는 동안 열린 DB 세션도 행
    잠금도 없습니다 (`docs/chat-transaction-flow.md`).
    """
    try:
        summary = await chat_service.create_summary(
            session_factory,
            user.app_user_id,
            session_id,
            client_request_id=body.client_request_id,
            summarizer=summarizer,
        )
    except chat_service.AppUserNotActiveError:
        # `current_app_user` 와 같은 문장 — 앱이 재로그인으로 알아듣는 자리입니다.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "다시 로그인해 주세요.") from None
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
    except chat_service.SummaryPersistenceError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {
                "code": "SUMMARY_PERSISTENCE_FAILED",
                "summary_id": str(exc.summary_id),
                "persistence_error_code": exc.persistence_error_code,
                "retry_with_fresh_client_request_id": (
                    exc.retry_with_fresh_client_request_id
                ),
            },
        ) from None
    except ChatSummaryError:
        # 원출력은 노출하지 않습니다 (O-14 와 같은 규칙).
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "요약을 만들지 못했습니다. 잠시 후 다시 시도해 주세요."
        ) from None
    return _summary_response(summary)


__all__ = ["get_chat_summarizer", "router"]
