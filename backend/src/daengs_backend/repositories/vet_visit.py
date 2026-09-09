"""vet_visits · vet_visit_drafts 조회·저장. 쿼리만 있고 판단은 없습니다 (#353).

"동의했나"·"이 강아지가 내 것인가"·"같은 영수증인가" 는 services 가 정합니다.
commit 도 하지 않습니다 — 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import VetVisit, VetVisitDraft

__all__ = [
    "add",
    "delete",
    "delete_draft",
    "expired_drafts",
    "find_duplicate",
    "get_by_client_event",
    "get_draft_by_client_event",
    "get_draft_by_sha",
    "get_draft_owned",
    "get_owned",
    "list_between",
    "sum_by_reason",
]


def add(session: AsyncSession, row: VetVisit | VetVisitDraft) -> None:
    session.add(row)


async def get_owned(
    session: AsyncSession, app_user_id: uuid.UUID, visit_id: uuid.UUID
) -> VetVisit | None:
    """**내 것일 때만** 돌려줍니다."""
    return await session.scalar(
        select(VetVisit).where(VetVisit.id == visit_id, VetVisit.app_user_id == app_user_id)
    )


async def delete(session: AsyncSession, visit: VetVisit) -> None:
    """확정된 기록 하나를 지웁니다. 사진 객체는 여기서 안 지웁니다 — 그 판단은
    services 의 몫입니다 (`delete_draft` 와 같은 자리 규칙)."""
    await session.delete(visit)


async def get_by_client_event(
    session: AsyncSession, app_user_id: uuid.UUID, client_event_id: uuid.UUID
) -> VetVisit | None:
    """멱등키로 찾습니다. UNIQUE (app_user_id, client_event_id) 가 있어 많아야 한 건입니다."""
    return await session.scalar(
        select(VetVisit).where(
            VetVisit.app_user_id == app_user_id,
            VetVisit.client_event_id == client_event_id,
        )
    )


async def list_between(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    start: date,
    end: date,
) -> list[VetVisit]:
    """`visited_on` 이 `[start, end]` 인 것, **최근 먼저.**"""
    return list(
        await session.scalars(
            select(VetVisit)
            .where(
                VetVisit.app_user_id == app_user_id,
                VetVisit.pet_id == pet_id,
                VetVisit.visited_on >= start,
                VetVisit.visited_on <= end,
            )
            .order_by(VetVisit.visited_on.desc(), VetVisit.id)
        )
    )


async def sum_by_reason(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    start: date,
    end: date,
) -> dict[str, int]:
    """사유별 누계. **없는 사유는 키가 없습니다** — `count_by_kind` 와 같은 규칙."""
    stmt = (
        select(VetVisit.reason_code, func.sum(VetVisit.total_krw))
        .where(
            VetVisit.app_user_id == app_user_id,
            VetVisit.pet_id == pet_id,
            VetVisit.visited_on >= start,
            VetVisit.visited_on <= end,
        )
        .group_by(VetVisit.reason_code)
    )
    return {code: int(total) for code, total in (await session.execute(stmt)).all()}


async def find_duplicate(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    visited_on: date,
    total_krw: int,
) -> VetVisit | None:
    """같은 날 같은 금액의 **확정된** 기록. 제약이 아니라 되묻는 근거입니다."""
    return await session.scalar(
        select(VetVisit)
        .where(
            VetVisit.app_user_id == app_user_id,
            VetVisit.pet_id == pet_id,
            VetVisit.visited_on == visited_on,
            VetVisit.total_krw == total_krw,
        )
        .limit(1)
    )


async def get_draft_owned(
    session: AsyncSession, app_user_id: uuid.UUID, draft_id: uuid.UUID
) -> VetVisitDraft | None:
    """**내 것일 때만** 돌려줍니다."""
    return await session.scalar(
        select(VetVisitDraft).where(
            VetVisitDraft.id == draft_id, VetVisitDraft.app_user_id == app_user_id
        )
    )


async def get_draft_by_client_event(
    session: AsyncSession, app_user_id: uuid.UUID, client_event_id: uuid.UUID
) -> VetVisitDraft | None:
    """멱등 ①. UNIQUE (app_user_id, client_event_id) 가 있어 많아야 한 건입니다."""
    return await session.scalar(
        select(VetVisitDraft).where(
            VetVisitDraft.app_user_id == app_user_id,
            VetVisitDraft.client_event_id == client_event_id,
        )
    )


async def get_draft_by_sha(
    session: AsyncSession, app_user_id: uuid.UUID, sha256: str
) -> VetVisitDraft | None:
    """멱등 ②. UNIQUE 가 아니라 **가장 최근 것**을 돌려줍니다 — 지우고 다시 올리는 것이
    정당합니다."""
    return await session.scalar(
        select(VetVisitDraft)
        .where(
            VetVisitDraft.app_user_id == app_user_id,
            VetVisitDraft.receipt_sha256 == sha256,
        )
        .order_by(VetVisitDraft.created_at.desc())
        .limit(1)
    )


async def delete_draft(session: AsyncSession, draft: VetVisitDraft) -> None:
    await session.delete(draft)


async def expired_drafts(
    session: AsyncSession, before: datetime, limit: int = 50
) -> list[VetVisitDraft]:
    """청소 대상. **한 요청이 몇 천 건을 지우지 않게** 상한을 둡니다 (docs §1 초안 청소)."""
    return list(
        await session.scalars(
            select(VetVisitDraft)
            .where(VetVisitDraft.created_at < before)
            .order_by(VetVisitDraft.created_at)
            .limit(limit)
        )
    )
