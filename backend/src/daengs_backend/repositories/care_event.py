"""care_events 조회·저장. 쿼리만 있고 판단은 없습니다 (#332).

"기간이 너무 넓은가"·"이 강아지가 내 것인가" 는 services 가 정합니다. commit 도 하지
않습니다 — 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import CareEvent

__all__ = [
    "add",
    "count_by_kind",
    "delete",
    "get_by_client_event",
    "get_owned",
    "list_between",
]


def add(session: AsyncSession, event: CareEvent) -> CareEvent:
    session.add(event)
    return event


async def get_owned(
    session: AsyncSession, app_user_id: uuid.UUID, event_id: uuid.UUID
) -> CareEvent | None:
    """**`actor_app_user_id` 가 `app_user_id` 인 것만** 돌려줍니다 — 지금 이 조건은 "챙긴
    사람" 이지 "소유자" 가 아닙니다 (docs/co-care.md). 삭제 권한을 이대로 둘지는 아직
    안 정해졌습니다 — 그 규칙은 이후 태스크가 다시 정합니다."""
    stmt = select(CareEvent).where(
        CareEvent.id == event_id, CareEvent.actor_app_user_id == app_user_id
    )
    return await session.scalar(stmt)


async def get_by_client_event(
    session: AsyncSession, pet_id: uuid.UUID, client_event_id: uuid.UUID
) -> CareEvent | None:
    """멱등키로 찾습니다. UNIQUE (pet_id, client_event_id) 가 있어 많아야 한 건입니다."""
    stmt = select(CareEvent).where(
        CareEvent.pet_id == pet_id, CareEvent.client_event_id == client_event_id
    )
    return await session.scalar(stmt)


async def list_between(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[CareEvent]:
    """`occurred_at` 이 `[start, end)` 인 것, **최근 먼저.** 같은 시각이면 `id` 로 한 번 더 정렬합니다."""
    stmt = (
        select(CareEvent)
        .where(
            CareEvent.actor_app_user_id == app_user_id,
            CareEvent.pet_id == pet_id,
            CareEvent.occurred_at >= start,
            CareEvent.occurred_at < end,
        )
        .order_by(CareEvent.occurred_at.desc(), CareEvent.id)
    )
    return list(await session.scalars(stmt))


async def count_by_kind(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> dict[str, int]:
    """종류별 건수. **없는 종류는 키가 없습니다** — 부르는 쪽이 `.get(kind, 0)` 을 씁니다."""
    stmt = (
        select(CareEvent.kind, func.count())
        .where(
            CareEvent.actor_app_user_id == app_user_id,
            CareEvent.pet_id == pet_id,
            CareEvent.occurred_at >= start,
            CareEvent.occurred_at < end,
        )
        .group_by(CareEvent.kind)
    )
    return {kind: int(n) for kind, n in (await session.execute(stmt)).all()}


async def delete(session: AsyncSession, event: CareEvent) -> None:
    await session.delete(event)
