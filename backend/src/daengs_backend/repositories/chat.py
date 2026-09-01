"""대화 세션·메시지·요약 조회·저장. 쿼리만 있고 판단은 없습니다.

"몇 개까지 남기나"·"어느 것을 밀어내나"·"내 것이 맞나"는 services 가 정합니다.
commit 도 하지 않습니다 — 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import ChatMessage, ChatSession, ChatSummary, Pet

__all__ = [
    "add_message",
    "add_session",
    "add_summary",
    "count_messages",
    "delete_messages_of",
    "delete_session",
    "get_owned_pet_id",
    "get_owned_session",
    "list_messages",
    "list_sessions",
    "list_summaries",
    "lock_owned_pet",
    "message_by_idempotency_key",
    "oldest_sessions_beyond",
    "summary_by_idempotency_key",
    "touch_session",
]


async def lock_owned_pet(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> uuid.UUID | None:
    """**내 강아지일 때만** id 를 돌려주고, 그 행을 잠급니다.

    두 가지를 한 번에 합니다.

    1. 소유권 확인 — `pets` 를 PK 로만 찾으면 남의 강아지 id 로 남의 대화를
       만들거나 읽을 수 있습니다 (`repositories/pet.py get_owned` 와 같은 이유).
    2. **5개 유지의 직렬화** — 같은 강아지로 동시에 두 개가 들어오면 둘 다
       "지금 5개"를 보고 둘 다 하나만 지워서 6개가 남습니다. 세션 행을 잠그는
       것으로는 못 막습니다. 아직 없는 행은 잠글 수 없기 때문입니다. 그래서
       **부모인 pets 행**을 잠가 그 강아지의 세션 생성을 한 줄로 세웁니다.
    """
    stmt = (
        select(Pet.id)
        .where(Pet.id == pet_id, Pet.app_user_id == app_user_id)
        .with_for_update()
    )
    return await session.scalar(stmt)


async def get_owned_pet_id(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> uuid.UUID | None:
    """소유권만 봅니다. **잠그지 않습니다** — 읽기 경로용입니다.

    목록 조회가 `FOR UPDATE` 를 걸면 그 강아지의 대화 생성이 조회 뒤에 줄을 섭니다.
    """
    stmt = select(Pet.id).where(Pet.id == pet_id, Pet.app_user_id == app_user_id)
    return await session.scalar(stmt)


async def list_sessions(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID, limit: int
) -> list[ChatSession]:
    """내 것 + 이 강아지, **최근 갱신 순.**

    `updated_at` 이 같을 수 있어(같은 초에 둘이 갱신되면) `id` 로 한 번 더
    정렬합니다 — 안 하면 순서가 호출마다 뒤집힐 수 있습니다.
    """
    stmt = (
        select(ChatSession)
        .where(ChatSession.app_user_id == app_user_id, ChatSession.pet_id == pet_id)
        .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def get_owned_session(
    session: AsyncSession, app_user_id: uuid.UUID, chat_session_id: uuid.UUID
) -> ChatSession | None:
    """**내 것일 때만** 돌려줍니다.

    소유자 조건을 이 함수 안에 묶어 둬서 부르는 쪽이 잊을 자리를 없앱니다.
    """
    stmt = select(ChatSession).where(
        ChatSession.id == chat_session_id,
        ChatSession.app_user_id == app_user_id,
    )
    return await session.scalar(stmt)


async def oldest_sessions_beyond(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID, keep: int
) -> list[ChatSession]:
    """최근 `keep` 개를 뺀 **나머지 오래된 것들.**

    한 개만 돌려주지 않는 이유는 상한을 낮췄거나 예전에 새던 적이 있으면 한 번에
    여럿을 걷어내야 하기 때문입니다. 보통은 0개 아니면 1개입니다.
    """
    stmt = (
        select(ChatSession)
        .where(ChatSession.app_user_id == app_user_id, ChatSession.pet_id == pet_id)
        .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
        .offset(keep)
    )
    return list(await session.scalars(stmt))


def add_session(session: AsyncSession, chat_session: ChatSession) -> ChatSession:
    session.add(chat_session)
    return chat_session


def touch_session(session: AsyncSession, chat_session: ChatSession) -> None:
    """`updated_at` 을 지금으로. **DB 가 시각을 찍습니다** — 앱 서버 시계를 믿지 않습니다.

    목록의 정렬 기준이자 5개 유지에서 무엇이 밀려나는지를 정하는 값이라, 서버마다
    다른 시계로 찍히면 순서가 서버에 따라 달라집니다.
    """
    chat_session.updated_at = func.now()


async def delete_session(session: AsyncSession, chat_session: ChatSession) -> None:
    """세션 하나를 지웁니다. 메시지는 FK 의 `ON DELETE CASCADE` 가 같이 지웁니다.

    **저장된 요약은 지워지지 않습니다** — `chat_summaries.session_id` 는
    `ON DELETE SET NULL` 이라 비워지기만 합니다 (`db/init/07_chats.sql`).
    """
    await session.delete(chat_session)


async def list_messages(
    session: AsyncSession, chat_session_id: uuid.UUID
) -> list[ChatMessage]:
    """대화 순서대로. 같은 시각이면 `id` 로 한 번 더 정렬합니다."""
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == chat_session_id)
        .order_by(ChatMessage.created_at, ChatMessage.id)
    )
    return list(await session.scalars(stmt))


async def count_messages(session: AsyncSession, chat_session_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(ChatMessage).where(
        ChatMessage.session_id == chat_session_id
    )
    return int(await session.scalar(stmt) or 0)


async def message_by_idempotency_key(
    session: AsyncSession, chat_session_id: uuid.UUID, client_message_id: str
) -> ChatMessage | None:
    """이미 들어온 그 메시지. 같은 탭을 두 번 눌렀을 때 되찾습니다."""
    stmt = select(ChatMessage).where(
        ChatMessage.session_id == chat_session_id,
        ChatMessage.client_message_id == client_message_id,
    )
    return await session.scalar(stmt)


def add_message(session: AsyncSession, message: ChatMessage) -> ChatMessage:
    session.add(message)
    return message


async def list_summaries(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> list[ChatSummary]:
    """보관함 목록. 최근 저장 순입니다.

    **`session_id` 로 거르지 않습니다.** 원본이 사라진 요약도 보관함에 남아야
    합니다 — 그것이 이 테이블을 따로 둔 이유입니다.
    """
    stmt = (
        select(ChatSummary)
        .where(ChatSummary.app_user_id == app_user_id, ChatSummary.pet_id == pet_id)
        .order_by(ChatSummary.created_at.desc(), ChatSummary.id.desc())
    )
    return list(await session.scalars(stmt))


async def summary_by_idempotency_key(
    session: AsyncSession, app_user_id: uuid.UUID, client_request_id: str
) -> ChatSummary | None:
    """이미 만든 그 요약. 요약 버튼을 두 번 눌렀을 때 되찾습니다.

    **생성 전에 먼저 부릅니다** — 여기서 걸리면 유료 호출을 아예 안 합니다.
    """
    stmt = select(ChatSummary).where(
        ChatSummary.app_user_id == app_user_id,
        ChatSummary.client_request_id == client_request_id,
    )
    return await session.scalar(stmt)


def add_summary(session: AsyncSession, summary: ChatSummary) -> ChatSummary:
    session.add(summary)
    return summary


async def delete_messages_of(session: AsyncSession, chat_session_id: uuid.UUID) -> None:
    """메시지만 지웁니다. 세션은 남습니다.

    FK 의 CASCADE 가 있어 세션을 지울 때는 필요 없고, 대화 하나를 비우는
    관리 작업에서만 씁니다.
    """
    await session.execute(
        sql_delete(ChatMessage).where(ChatMessage.session_id == chat_session_id)
    )
