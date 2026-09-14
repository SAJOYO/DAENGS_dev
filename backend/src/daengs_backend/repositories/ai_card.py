"""R — AI 도감 카드 쿼리. 판단은 `services/ai_card.py`·`services/ai_card_quota.py` 가 합니다."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import AiCard


def add(session: AsyncSession, card: AiCard) -> AiCard:
    session.add(card)
    return card


async def get_owned(
    session: AsyncSession, app_user_id: uuid.UUID, card_id: uuid.UUID, *, for_update: bool = False
) -> AiCard | None:
    """**내 것일 때만** 돌려줍니다. 남의 것도 없는 것과 같은 None 입니다."""
    stmt = select(AiCard).where(AiCard.id == card_id, AiCard.app_user_id == app_user_id)
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def get_for_update(session: AsyncSession, card_id: uuid.UUID) -> AiCard | None:
    """**소유자를 안 봅니다.** 백그라운드 생성이 자기가 만든 행을 다시 잡을 때만 씁니다."""
    return await session.scalar(select(AiCard).where(AiCard.id == card_id).with_for_update())


async def list_for_owner(session: AsyncSession, app_user_id: uuid.UUID, *, limit: int = 200) -> list[AiCard]:
    stmt = (
        select(AiCard)
        .where(AiCard.app_user_id == app_user_id)
        .order_by(AiCard.created_at.desc())
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def has_generating(session: AsyncSession, app_user_id: uuid.UUID) -> bool:
    stmt = select(AiCard.id).where(AiCard.app_user_id == app_user_id, AiCard.status == "generating").limit(1)
    return await session.scalar(stmt) is not None


async def count_ready_since(session: AsyncSession, app_user_id: uuid.UUID, since: datetime) -> int:
    stmt = select(func.count()).where(
        AiCard.app_user_id == app_user_id, AiCard.status == "ready", AiCard.created_at >= since
    )
    return int(await session.scalar(stmt) or 0)


async def expire_generating(
    session: AsyncSession, app_user_id: uuid.UUID, *, created_before: datetime, now: datetime
) -> int:
    """정리 기준보다 오래된 `generating` 을 `failed`/`interrupted` 로 바꿉니다.

    배포 재시작과 겹쳐 사라진 백그라운드 작업의 행입니다. 커밋은 부르는 쪽이 합니다.
    """
    result = await session.execute(
        update(AiCard)
        .where(
            AiCard.app_user_id == app_user_id,
            AiCard.status == "generating",
            AiCard.created_at < created_before,
        )
        .values(status="failed", error_code="interrupted", updated_at=now)
    )
    return result.rowcount or 0


async def find_ready_by_storage_key(session: AsyncSession, storage_key: str) -> AiCard | None:
    """bridge 전용. ⚠️ 소유자 조건이 없습니다 — 대신 **backend 가 실제로 저장한 키인지**를 봅니다."""
    return await session.scalar(
        select(AiCard).where(AiCard.storage_key == storage_key, AiCard.status == "ready")
    )


async def list_for_owner_for_update(session: AsyncSession, app_user_id: uuid.UUID) -> list[AiCard]:
    stmt = select(AiCard).where(AiCard.app_user_id == app_user_id).with_for_update()
    return list(await session.scalars(stmt))


async def delete(session: AsyncSession, card: AiCard) -> None:
    await session.delete(card)


async def delete_all_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """⚠️ `app_users` CASCADE 에 기대면 안 됩니다 — 탈퇴는 그 행을 남깁니다."""
    result = await session.execute(sql_delete(AiCard).where(AiCard.app_user_id == app_user_id))
    return result.rowcount or 0
