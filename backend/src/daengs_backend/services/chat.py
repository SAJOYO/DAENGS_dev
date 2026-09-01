"""대화 기록의 규칙. 트랜잭션 경계도 여기입니다.

라우터는 HTTP 만 보고, 리포지토리는 쿼리만 합니다. "몇 개까지 남기나"·"어느 것을
밀어내나"·"두 번 눌렀을 때 어떻게 하나"는 전부 여기 모입니다.

**저장은 사용자+강아지 스코프입니다.** 소유권은 서버가 확인하고, 요청 본문의
`app_user_id` 같은 신원 필드는 받지 않습니다 — 인증된 principal 만 씁니다.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import ChatMessage, ChatSession, ChatSummary
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityStatus,
)
from daengs_backend.repositories import chat as chat_repo
from daengs_backend.services.chat_summary import (
    PROMPT_VERSION,
    SUMMARY_MODEL_ID,
    ChatSummaryError,
    GeminiChatSummarizer,
    render_transcript,
)

#: 사용자+강아지마다 남기는 **대화 세션**의 수. 메시지 5개가 아닙니다.
#:
#: ⚠️ 앱이 이 숫자를 박아 두지 않도록 목록 응답에 같이 실어 보냅니다
#: (`MAX_PETS_PER_USER` 를 `PetListResponse.max_pets` 로 보내는 것과 같은 이유).
MAX_SESSIONS_PER_PET = 5

#: 카드 제목으로 자를 길이. `chat_sessions.title` 은 VARCHAR(120) 입니다.
_TITLE_MAX = 120

#: 답이 나오지 않은 상태들. **이 상태의 답은 저장하지 않습니다** —
#: 실패를 완료된 답처럼 남기면 나중에 목록에서 그것을 진짜 답으로 읽습니다.
_UNANSWERED = frozenset(
    {AssistantStatus.FAILED, AssistantStatus.CLARIFY, AssistantStatus.PENDING}
)


class ChatSessionNotFoundError(Exception):
    """내 대화가 아니거나 없습니다.

    **남의 것일 때도 이 예외입니다.** 403 으로 나누면 "그 id 는 존재한다"를
    알려 주는 셈이라, 없는 것과 남의 것을 같은 404 로 뭉갭니다
    (`services/pet.py PetNotFoundError` 와 같은 판단).
    """


class PetNotOwnedError(Exception):
    """내 강아지가 아니거나 없습니다. 라우터가 404 로 바꿉니다."""


class EmptyConversationError(Exception):
    """요약할 말이 없습니다. 라우터가 409 로 바꿉니다.

    빈 대화를 모델에 보내면 모델은 **없는 대화를 지어냅니다.** 부르기 전에 막습니다.
    """


def build_title(first_message: str) -> str:
    """첫 사용자 메시지에서 카드 제목을 만듭니다. **LLM 을 부르지 않습니다.**

    제목 때문에 대화마다 생성 비용을 물 이유가 없고, 요약은 사용자가 누를 때만
    만든다는 것이 이 카드의 전제입니다. 줄바꿈은 카드가 한 줄로 보여 주므로
    공백으로 접습니다.
    """
    flattened = " ".join(first_message.split())
    if not flattened:
        return "새 대화"
    if len(flattened) <= _TITLE_MAX:
        return flattened
    # 자른 티를 냅니다 — 잘린 문장이 원문인 것처럼 보이지 않게.
    return flattened[: _TITLE_MAX - 1] + "…"


def categories_of(response: AssistantResponse) -> list[str]:
    """이 답에 실제로 기여한 능력 이름들. **라우팅 메타데이터가 원천입니다.**

    클라이언트가 보낸 값이나 질문 속 낱말로 정하지 않습니다 — 키워드 하드 라우팅은
    D-031 이 금지한 것이고, 배지가 틀리면 사용자는 답 자체를 의심합니다.

    **답을 낸 능력만 셉니다.** 기권(ABSTAINED)·거절(REFUSED)·오류는 배지에 넣지
    않습니다. 넣으면 "훈련이 답했다" 는 배지가 붙은 카드를 열었을 때 훈련은 아무
    말도 안 했던 것이 됩니다. 순서는 실행 순서를 그대로 둡니다.
    """
    seen: list[str] = []
    for result in response.results:
        if result.status is not CapabilityStatus.OK:
            continue
        name = result.capability.value
        if name not in seen:
            seen.append(name)
    return seen


async def _lock_owned_pet(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> None:
    """쓰기 경로용 — 소유권을 확인하고 그 강아지의 세션 생성을 직렬화합니다."""
    if await chat_repo.lock_owned_pet(session, app_user_id, pet_id) is None:
        raise PetNotOwnedError


async def _require_owned_pet(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> None:
    """읽기 경로용 — 소유권만 봅니다. **잠그지 않습니다.**

    목록 조회가 행을 잠그면 그 강아지의 대화 생성이 조회 트랜잭션 뒤에 줄을 섭니다.
    잠금이 필요한 것은 5개 유지를 판정하는 쪽뿐입니다.
    """
    if await chat_repo.get_owned_pet_id(session, app_user_id, pet_id) is None:
        raise PetNotOwnedError


async def _require_owned_session(
    session: AsyncSession, app_user_id: uuid.UUID, chat_session_id: uuid.UUID
) -> ChatSession:
    found = await chat_repo.get_owned_session(session, app_user_id, chat_session_id)
    if found is None:
        raise ChatSessionNotFoundError
    return found


async def list_sessions(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> list[ChatSession]:
    """최근 다섯 개, 최근 갱신 순.

    소유권을 여기서도 확인합니다 — 남의 강아지 id 로 목록을 부르면 빈 목록이
    아니라 404 여야 합니다. "그 강아지는 있지만 대화가 없다"를 알려 주지 않습니다.
    """
    await _require_owned_pet(session, app_user_id, pet_id)
    return await chat_repo.list_sessions(
        session, app_user_id, pet_id, MAX_SESSIONS_PER_PET
    )


async def create_session(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    title: str | None = None,
) -> ChatSession:
    """새 대화. **여섯 번째를 만들면 그 스코프의 가장 오래된 것이 사라집니다.**

    지우는 범위는 `app_user_id + pet_id` 한 쌍뿐입니다 — 다른 사용자의 것도,
    같은 사용자의 다른 강아지 것도 건드리지 않습니다.

    `_lock_owned_pet` 이 `pets` 행을 잠그기 때문에(FOR UPDATE) 같은 강아지로
    동시에 두 개가 들어와도 한 줄로 세워집니다. 안 그러면 둘 다 "지금 5개"를 보고
    둘 다 하나만 지워서 **6개가 남습니다.**
    """
    await _lock_owned_pet(session, app_user_id, pet_id)

    chat_session = ChatSession(
        app_user_id=app_user_id,
        pet_id=pet_id,
        title=build_title(title or ""),
        agent_categories=[],
    )
    chat_repo.add_session(session, chat_session)
    await session.flush()

    # 새로 만든 것까지 세어 상한을 넘는 만큼 걷어냅니다. 방금 만든 것은
    # `updated_at` 이 가장 커서 절대 걸리지 않습니다.
    for stale in await chat_repo.oldest_sessions_beyond(
        session, app_user_id, pet_id, MAX_SESSIONS_PER_PET
    ):
        await chat_repo.delete_session(session, stale)

    await session.commit()
    return chat_session


async def get_session_with_messages(
    session: AsyncSession, app_user_id: uuid.UUID, chat_session_id: uuid.UUID
) -> tuple[ChatSession, list[ChatMessage]]:
    """카드를 눌렀을 때. 그 대화의 메시지를 순서대로 되살립니다."""
    chat_session = await _require_owned_session(session, app_user_id, chat_session_id)
    messages = await chat_repo.list_messages(session, chat_session.id)
    return chat_session, messages


async def delete_session(
    session: AsyncSession, app_user_id: uuid.UUID, chat_session_id: uuid.UUID
) -> None:
    """손으로 지웁니다. 메시지도 같이 사라집니다(FK CASCADE).

    **저장해 둔 요약은 남습니다** — `chat_summaries.session_id` 가 SET NULL 이라
    원본 연결만 끊깁니다.
    """
    chat_session = await _require_owned_session(session, app_user_id, chat_session_id)
    await chat_repo.delete_session(session, chat_session)
    await session.commit()


async def append_exchange(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    chat_session_id: uuid.UUID,
    *,
    question: str,
    response: AssistantResponse,
    client_message_id: str | None = None,
) -> list[ChatMessage]:
    """질문과 답을 한 트랜잭션에 붙입니다.

    **같은 `client_message_id` 가 이미 있으면 아무것도 새로 만들지 않습니다** —
    두 번 눌렀거나 네트워크가 재시도한 것이고, 그때 답이 두 벌 쌓이면 목록이
    거짓말을 합니다. 이미 있는 그 교환을 그대로 돌려줍니다.

    **답이 안 나온 상태(FAILED · CLARIFY · PENDING)면 질문만 남깁니다.** 실패를
    완료된 답처럼 저장하면 나중에 그것을 진짜 답으로 읽습니다.
    """
    chat_session = await _require_owned_session(session, app_user_id, chat_session_id)

    if client_message_id is not None:
        existing = await chat_repo.message_by_idempotency_key(
            session, chat_session.id, client_message_id
        )
        if existing is not None:
            # 이미 처리된 요청입니다. 그때 저장된 것을 그대로 보여 줍니다.
            return await chat_repo.list_messages(session, chat_session.id)

    stored: list[ChatMessage] = [
        chat_repo.add_message(
            session,
            ChatMessage(
                session_id=chat_session.id,
                role="user",
                content=question,
                agent_categories=[],
                client_message_id=client_message_id,
                request_id=response.request_id,
            ),
        )
    ]

    if response.status not in _UNANSWERED:
        stored.append(
            chat_repo.add_message(
                session,
                ChatMessage(
                    session_id=chat_session.id,
                    role="assistant",
                    content=response.message,
                    agent_categories=categories_of(response),
                    assistant_status=response.status.value,
                    request_id=response.request_id,
                ),
            )
        )

    # 세션의 배지는 지금까지 관여한 능력의 **합집합**입니다. 한 대화에서 훈련과
    # 생활을 모두 물었으면 카드에 배지가 둘 붙어야 합니다 — 하나로 접으면
    # 어느 쪽이든 틀립니다.
    merged = list(chat_session.agent_categories)
    for name in categories_of(response):
        if name not in merged:
            merged.append(name)
    chat_session.agent_categories = merged

    # 목록의 정렬 기준이자 5개 유지의 기준입니다. 말이 오갔으면 최근으로 올립니다.
    chat_repo.touch_session(session, chat_session)

    if chat_session.title == "새 대화" and question.strip():
        chat_session.title = build_title(question)

    try:
        await session.commit()
    except IntegrityError:
        # 같은 멱등 키가 **동시에** 두 번 들어온 경우입니다. 위의 조회로는 못 잡는
        # 좁은 틈이라 유니크 인덱스가 마지막으로 막습니다.
        await session.rollback()
        return await chat_repo.list_messages(session, chat_session.id)

    return stored


async def create_summary(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    chat_session_id: uuid.UUID,
    *,
    client_request_id: str,
    summarizer: GeminiChatSummarizer | None = None,
) -> ChatSummary:
    """`AI 요약 후 저장`. **사용자가 누를 때만 돕니다.**

    답변마다 자동으로 다시 만들지 않습니다 — 유료 호출이고, 사용자가 저장하기로
    한 시점의 대화를 접는 것이 이 기능의 뜻입니다.

    **멱등 키를 먼저 봅니다.** 두 번 눌렀으면 여기서 걸려 모델을 아예 안 부릅니다.
    """
    existing = await chat_repo.summary_by_idempotency_key(
        session, app_user_id, client_request_id
    )
    if existing is not None:
        return existing

    chat_session = await _require_owned_session(session, app_user_id, chat_session_id)
    messages = await chat_repo.list_messages(session, chat_session.id)
    if not messages:
        raise EmptyConversationError

    # **이 세션의 메시지만** 넘어갑니다. 다른 대화를 끌어올 경로가 없습니다.
    transcript = render_transcript([(m.role, m.content) for m in messages])
    draft = await (summarizer or GeminiChatSummarizer()).summarize(transcript=transcript)

    summary = ChatSummary(
        app_user_id=app_user_id,
        pet_id=chat_session.pet_id,
        session_id=chat_session.id,
        title=draft.title,
        question_summary=draft.question_summary,
        answer_summary=draft.answer_summary,
        key_points=draft.key_points,
        cautions=draft.cautions,
        source_citations=draft.source_citations,
        # 요약의 배지는 원본 세션의 것을 그대로 물려받습니다. 요약 모델에게
        # 능력 이름을 고르게 하지 않습니다 — 그건 라우팅이 이미 정한 사실입니다.
        agent_categories=list(chat_session.agent_categories),
        model=SUMMARY_MODEL_ID,
        prompt_version=PROMPT_VERSION,
        source_message_count=len(messages),
        client_request_id=client_request_id,
    )
    chat_repo.add_summary(session, summary)

    try:
        await session.commit()
    except IntegrityError:
        # 같은 키로 동시에 두 번 눌린 경우. 먼저 들어간 것을 돌려줍니다.
        await session.rollback()
        winner = await chat_repo.summary_by_idempotency_key(
            session, app_user_id, client_request_id
        )
        if winner is None:
            raise ChatSummaryError("summary insert conflicted but no row was found") from None
        return winner

    return summary


async def list_summaries(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> list[ChatSummary]:
    """보관함. 원본이 사라진 요약도 그대로 나옵니다."""
    await _require_owned_pet(session, app_user_id, pet_id)
    return await chat_repo.list_summaries(session, app_user_id, pet_id)


__all__ = [
    "MAX_SESSIONS_PER_PET",
    "ChatSessionNotFoundError",
    "EmptyConversationError",
    "PetNotOwnedError",
    "append_exchange",
    "build_title",
    "categories_of",
    "create_session",
    "create_summary",
    "delete_session",
    "get_session_with_messages",
    "list_sessions",
    "list_summaries",
]
