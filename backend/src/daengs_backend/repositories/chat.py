"""Queries for chat sessions, turns, and summary reservations. No commits here."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import ChatSession, ChatSummary, ChatTurn, Pet
from daengs_backend.repositories import pet as pet_repo


async def get_accessible_pet_id(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> uuid.UUID | None:
    """그 아이 얘기를 해도 되는가 — **구성원(대표 ∪ 돌보미)이면 됩니다** (docs/co-care.md §2).

    대화 **세션**의 소유는 별개입니다 — `chat_sessions.app_user_id` 가 따로 걸려 있어
    아빠의 대화가 나에게 새지 않습니다. 여기서 보는 것은 "그 아이를 돌보는 사람인가" 뿐입니다.
    """
    return await session.scalar(
        select(Pet.id).where(Pet.id == pet_id, pet_repo.member_condition(app_user_id))
    )


async def lock_accessible_pet(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> uuid.UUID | None:
    """같은 판정을 행 잠금까지. 동시에 두 세션을 여는 것을 `pets` 행으로 줄 세웁니다."""
    return await session.scalar(
        select(Pet.id)
        .where(Pet.id == pet_id, pet_repo.member_condition(app_user_id))
        .with_for_update(of=Pet)
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
        # The caller may already have loaded this row before waiting for the lock.
        # Refresh the identity-map instance so first-activation and category decisions
        # use the state committed by the transaction that released the lock.
        .execution_options(populate_existing=True)
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


async def list_capacity_turns(session: AsyncSession, session_id: uuid.UUID) -> list[ChatTurn]:
    """Rows that consume the completed-turn and reserved transcript budgets."""
    rows = await session.scalars(
        select(ChatTurn)
        .where(
            ChatTurn.session_id == session_id,
            ChatTurn.processing_status.in_(("processing", "completed")),
        )
        .order_by(ChatTurn.created_at, ChatTurn.id)
    )
    return list(rows)


async def list_recent_completed_turns(
    session: AsyncSession, session_id: uuid.UUID, *, limit: int
) -> list[ChatTurn]:
    """Turn Resolver 후보와 대기 되묻기의 원본 (#416 Task 7).

    `DESC … LIMIT` 으로 가져오는 이유는 원하는 것이 **최신 `limit` 개**이기 때문이다 —
    `ASC` 로는 가장 오래된 `limit` 개가 나와 전혀 다른 결과가 된다. (`chat_turns_session_order_idx`
    는 어느 방향으로 정렬해도 그대로 탄다 — btree 는 역방향 스캔이 정방향과 같은 비용이라,
    `ORDER BY created_at DESC, id DESC` 도 이 인덱스를 그대로 쓴다.) 잘라낸 뒤에는 뒤집어
    오래된 순으로 돌려준다 — Resolver 가 후보를 `U1/A1, U2/A2…` 로 번호 매기기 때문이다.
    순서가 뒤집히면 모델이 고른 번호가 엉뚱한 turn 에 붙는다.

    `processing_status == 'completed'` 만 보므로, 방금 예약한(아직 `processing`인) turn
    은 여기 안 걸린다 — 자기 자신을 자기 맥락으로 삼는 사고가 애초에 안 생긴다.

    `limit >= 1` 을 전제한다 — 대기 되묻기 판정(`pending_clarification_of`)이 잘림에도
    불변인 것은 `turns[-1]`(가장 최신 완료 turn)이 어떤 `limit >= 1` 에도 항상 남기
    때문이다. `limit=0` 을 넘기면 그 전제가 깨져 대기 되묻기가 늘 조용히 사라진다.
    """
    rows = await session.scalars(
        select(ChatTurn)
        .where(
            ChatTurn.session_id == session_id,
            ChatTurn.processing_status == "completed",
        )
        .order_by(ChatTurn.created_at.desc(), ChatTurn.id.desc())
        .limit(limit)
    )
    return list(reversed(rows.all()))


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


async def fail_stale_turns(
    session: AsyncSession, *, session_id: uuid.UUID, cutoff: datetime
) -> int:
    """Same lazy recovery as ``fail_stale_summaries``, scoped to one session."""
    result = await session.execute(
        update(ChatTurn)
        .where(
            ChatTurn.session_id == session_id,
            ChatTurn.processing_status == "processing",
            ChatTurn.processing_started_at < cutoff,
        )
        .values(
            processing_status="failed",
            error_code="STALE_PROCESSING",
            completed_at=func.now(),
        )
    )
    return int(result.rowcount or 0)


async def prune_failed_turns(session: AsyncSession, *, session_id: uuid.UUID, keep: int) -> int:
    """Delete failed rows older than the newest ``keep`` by ``created_at, id``."""
    oldest_ids = (
        select(ChatTurn.id)
        .where(
            ChatTurn.session_id == session_id,
            ChatTurn.processing_status == "failed",
        )
        .order_by(ChatTurn.created_at.desc(), ChatTurn.id.desc())
        .offset(keep)
    )
    result = await session.execute(delete(ChatTurn).where(ChatTurn.id.in_(oldest_ids)))
    return int(result.rowcount or 0)


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


async def get_owned_summary(
    session: AsyncSession, app_user_id: uuid.UUID, summary_id: uuid.UUID
) -> ChatSummary | None:
    return await session.scalar(
        select(ChatSummary).where(
            ChatSummary.id == summary_id, ChatSummary.app_user_id == app_user_id
        )
    )


async def delete_summary(session: AsyncSession, summary: ChatSummary) -> None:
    await session.delete(summary)


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
