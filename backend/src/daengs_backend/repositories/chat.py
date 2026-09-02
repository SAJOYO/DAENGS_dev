"""Queries for chat sessions, turns, and summary reservations. No commits here."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import ChatSession, ChatSummary, ChatTurn, Pet


async def get_owned_pet_id(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> uuid.UUID | None:
    return await session.scalar(
        select(Pet.id).where(Pet.id == pet_id, Pet.app_user_id == app_user_id)
    )


async def lock_owned_pet(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> uuid.UUID | None:
    return await session.scalar(
        select(Pet.id)
        .where(Pet.id == pet_id, Pet.app_user_id == app_user_id)
        .with_for_update()
    )


async def get_owned_session(
    session: AsyncSession, app_user_id: uuid.UUID, session_id: uuid.UUID
) -> ChatSession | None:
    return await session.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id, ChatSession.app_user_id == app_user_id
        )
    )


async def get_owned_session_for_update(
    session: AsyncSession, app_user_id: uuid.UUID, session_id: uuid.UUID
) -> ChatSession | None:
    return await session.scalar(
        select(ChatSession)
        .where(ChatSession.id == session_id, ChatSession.app_user_id == app_user_id)
        .with_for_update()
    )


async def get_draft(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> ChatSession | None:
    return await session.scalar(
        select(ChatSession).where(
            ChatSession.app_user_id == app_user_id,
            ChatSession.pet_id == pet_id,
            ChatSession.last_message_at.is_(None),
        )
    )


async def list_active_sessions(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID, limit: int
) -> list[ChatSession]:
    rows = await session.scalars(
        select(ChatSession)
        .where(
            ChatSession.app_user_id == app_user_id,
            ChatSession.pet_id == pet_id,
            ChatSession.last_message_at.is_not(None),
        )
        .order_by(ChatSession.last_message_at.desc(), ChatSession.id.desc())
        .limit(limit)
    )
    return list(rows)


async def oldest_active_sessions_beyond(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID, keep: int
) -> list[ChatSession]:
    rows = await session.scalars(
        select(ChatSession)
        .where(
            ChatSession.app_user_id == app_user_id,
            ChatSession.pet_id == pet_id,
            ChatSession.last_message_at.is_not(None),
        )
        .order_by(ChatSession.last_message_at.desc(), ChatSession.id.desc())
        .offset(keep)
    )
    return list(rows)


def add_session(session: AsyncSession, chat_session: ChatSession) -> ChatSession:
    session.add(chat_session)
    return chat_session


async def delete_session(session: AsyncSession, chat_session: ChatSession) -> None:
    await session.delete(chat_session)


def touch_active_session(
    session: AsyncSession, chat_session: ChatSession, categories: list[str]
) -> None:
    chat_session.last_message_at = func.now()
    chat_session.agent_categories = categories


async def get_turn_by_client_id(
    session: AsyncSession, session_id: uuid.UUID, client_message_id: uuid.UUID
) -> ChatTurn | None:
    return await session.scalar(
        select(ChatTurn).where(
            ChatTurn.session_id == session_id,
            ChatTurn.client_message_id == client_message_id,
        )
    )


async def get_owned_turn(
    session: AsyncSession, app_user_id: uuid.UUID, turn_id: uuid.UUID
) -> tuple[ChatTurn, ChatSession] | None:
    row = (
        await session.execute(
            select(ChatTurn, ChatSession)
            .join(ChatSession, ChatSession.id == ChatTurn.session_id)
            .where(ChatTurn.id == turn_id, ChatSession.app_user_id == app_user_id)
        )
    ).one_or_none()
    return (row[0], row[1]) if row is not None else None


async def count_reserved_turns(session: AsyncSession, session_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(ChatTurn)
            .where(
                ChatTurn.session_id == session_id,
                ChatTurn.processing_status.in_(("processing", "completed")),
            )
        )
        or 0
    )


async def list_turns(
    session: AsyncSession, session_id: uuid.UUID, *, completed_only: bool = False
) -> list[ChatTurn]:
    stmt = select(ChatTurn).where(ChatTurn.session_id == session_id)
    if completed_only:
        stmt = stmt.where(ChatTurn.processing_status == "completed")
    rows = await session.scalars(stmt.order_by(ChatTurn.created_at, ChatTurn.id))
    return list(rows)


def add_turn(session: AsyncSession, turn: ChatTurn) -> ChatTurn:
    session.add(turn)
    return turn


async def complete_turn_if_processing(
    session: AsyncSession,
    turn_id: uuid.UUID,
    *,
    assistant_content: str,
    assistant_status: str,
    request_id: str,
    agent_categories: list[str],
    public_response: dict[str, Any],
) -> ChatTurn | None:
    stmt = (
        update(ChatTurn)
        .where(ChatTurn.id == turn_id, ChatTurn.processing_status == "processing")
        .values(
            processing_status="completed",
            assistant_content=assistant_content,
            assistant_status=assistant_status,
            request_id=request_id,
            agent_categories=agent_categories,
            public_response=public_response,
            completed_at=func.now(),
        )
        .returning(ChatTurn)
    )
    return (await session.scalars(stmt)).one_or_none()


async def fail_turn_if_processing(
    session: AsyncSession, turn_id: uuid.UUID, *, error_code: str
) -> ChatTurn | None:
    stmt = (
        update(ChatTurn)
        .where(ChatTurn.id == turn_id, ChatTurn.processing_status == "processing")
        .values(processing_status="failed", error_code=error_code, completed_at=func.now())
        .returning(ChatTurn)
    )
    return (await session.scalars(stmt)).one_or_none()


async def list_completed_summaries(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> list[ChatSummary]:
    rows = await session.scalars(
        select(ChatSummary)
        .where(
            ChatSummary.app_user_id == app_user_id,
            ChatSummary.pet_id == pet_id,
            ChatSummary.processing_status == "completed",
        )
        .order_by(ChatSummary.created_at.desc(), ChatSummary.id.desc())
    )
    return list(rows)


async def summary_by_request_id(
    session: AsyncSession, app_user_id: uuid.UUID, client_request_id: uuid.UUID
) -> ChatSummary | None:
    return await session.scalar(
        select(ChatSummary).where(
            ChatSummary.app_user_id == app_user_id,
            ChatSummary.client_request_id == client_request_id,
        )
    )


async def active_summary_for_source(
    session: AsyncSession, source_session_id: uuid.UUID, source_turn_count: int
) -> ChatSummary | None:
    return await session.scalar(
        select(ChatSummary).where(
            ChatSummary.source_session_id == source_session_id,
            ChatSummary.source_turn_count == source_turn_count,
            ChatSummary.processing_status.in_(("processing", "completed")),
        )
    )


async def fail_stale_summaries(
    session: AsyncSession,
    *,
    source_session_id: uuid.UUID,
    cutoff: datetime,
) -> int:
    result = await session.execute(
        update(ChatSummary)
        .where(
            ChatSummary.source_session_id == source_session_id,
            ChatSummary.processing_status == "processing",
            ChatSummary.processing_started_at < cutoff,
        )
        .values(
            processing_status="failed",
            error_code="STALE_PROCESSING",
            completed_at=func.now(),
        )
    )
    return int(result.rowcount or 0)


def add_summary(session: AsyncSession, summary: ChatSummary) -> ChatSummary:
    session.add(summary)
    return summary


async def complete_summary_if_processing(
    session: AsyncSession,
    summary_id: uuid.UUID,
    *,
    title: str,
    question_summary: str,
    answer_summary: str,
    key_points: list[str],
    cautions: list[str],
    source_citations: list[dict[str, Any]],
    model: str,
    prompt_version: str,
) -> ChatSummary | None:
    stmt = (
        update(ChatSummary)
        .where(ChatSummary.id == summary_id, ChatSummary.processing_status == "processing")
        .values(
            processing_status="completed",
            title=title,
            question_summary=question_summary,
            answer_summary=answer_summary,
            key_points=key_points,
            cautions=cautions,
            source_citations=source_citations,
            model=model,
            prompt_version=prompt_version,
            completed_at=func.now(),
        )
        .returning(ChatSummary)
    )
    return (await session.scalars(stmt)).one_or_none()


async def fail_summary_if_processing(
    session: AsyncSession, summary_id: uuid.UUID, *, error_code: str
) -> ChatSummary | None:
    stmt = (
        update(ChatSummary)
        .where(ChatSummary.id == summary_id, ChatSummary.processing_status == "processing")
        .values(processing_status="failed", error_code=error_code, completed_at=func.now())
        .returning(ChatSummary)
    )
    return (await session.scalars(stmt)).one_or_none()


async def delete_all_for_user(session: AsyncSession, app_user_id: uuid.UUID) -> None:
    """Explicit withdrawal cleanup; app_users rows are retained, so FK cascade cannot run."""
    await session.execute(delete(ChatSummary).where(ChatSummary.app_user_id == app_user_id))
    await session.execute(delete(ChatSession).where(ChatSession.app_user_id == app_user_id))
