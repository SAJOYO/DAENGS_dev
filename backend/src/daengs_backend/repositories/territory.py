"""점령지 인증 원장 DAO. 소유권 필터 없는 조회는 워커·bridge 전용입니다."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from daengs_backend.models.territory import TerritoryAttempt


def _owned(app_user_id: uuid.UUID):
    return (
        select(TerritoryAttempt)
        .where(TerritoryAttempt.app_user_id == app_user_id)
        .options(selectinload(TerritoryAttempt.verified_visit))
    )


async def get_owned(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    attempt_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> TerritoryAttempt | None:
    stmt = _owned(app_user_id).where(TerritoryAttempt.id == attempt_id)
    if for_update:
        stmt = stmt.with_for_update(of=TerritoryAttempt)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_client_capture(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    client_capture_id: uuid.UUID,
) -> TerritoryAttempt | None:
    stmt = _owned(app_user_id).where(TerritoryAttempt.client_capture_id == client_capture_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_for_decision(
    session: AsyncSession,
    attempt_id: uuid.UUID,
) -> TerritoryAttempt | None:
    """VLM 워커가 결과를 반영할 때 쓰는 행 잠금 조회."""
    stmt = (
        select(TerritoryAttempt)
        .where(TerritoryAttempt.id == attempt_id)
        .options(selectinload(TerritoryAttempt.verified_visit))
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_for_worker(
    session: AsyncSession,
    attempt_id: uuid.UUID,
) -> TerritoryAttempt | None:
    """워커가 외부 호출 전에 고정 증거만 복사하는 잠금 없는 조회."""
    stmt = select(TerritoryAttempt).where(TerritoryAttempt.id == attempt_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_pending_by_storage_key(
    session: AsyncSession,
    storage_key: str,
) -> TerritoryAttempt | None:
    """인증 없는 local bridge가 발급된 미사용 키인지 확인합니다."""
    stmt = select(TerritoryAttempt).where(
        TerritoryAttempt.photo_storage_key == storage_key,
        TerritoryAttempt.status == "PENDING_UPLOAD",
    )
    return (await session.execute(stmt)).scalar_one_or_none()
