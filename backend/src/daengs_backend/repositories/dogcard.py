"""R — 도감 카드 쿼리. 판단은 `services/dogcard.py` 가 합니다."""

from __future__ import annotations

import uuid

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import DogCard


def add(session: AsyncSession, card: DogCard) -> DogCard:
    session.add(card)
    return card


async def get_any(
    session: AsyncSession, card_id: uuid.UUID, *, for_update: bool = False
) -> DogCard | None:
    """**소유자를 안 봅니다.** upsert 가 "남이 이미 가진 id 인가" 를 물을 때만 씁니다.

    ⚠️ 이 함수를 조회에 쓰면 남의 카드가 샙니다. 읽기는 `get_owned` 를 쓰세요.
    """
    stmt = select(DogCard).where(DogCard.id == card_id)
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def get_owned(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    card_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> DogCard | None:
    """**내 것일 때만** 돌려줍니다. 소유자 조건을 함수 안에 묶어 둡니다."""
    stmt = select(DogCard).where(
        DogCard.id == card_id, DogCard.app_user_id == app_user_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def list_for_owner(
    session: AsyncSession, app_user_id: uuid.UUID, *, limit: int = 500
) -> list[DogCard]:
    """내 카드 전부, **최근에 뽑은 것부터.**

    상한이 큰 이유는 이 목록이 **폰을 바꿨을 때 복원**에 쓰이기 때문입니다 —
    나눠 받으면 앱이 페이지를 이어 붙이는 코드를 따로 가져야 합니다.
    """
    stmt = (
        select(DogCard)
        .where(DogCard.app_user_id == app_user_id)
        .order_by(DogCard.drawn_at.desc())
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def find_by_face_key(session: AsyncSession, storage_key: str) -> DogCard | None:
    """저장소 키 하나로 행을 찾습니다. **bridge 전용입니다.**

    ⚠️ **소유자 조건이 없습니다.** bridge 는 인증 헤더를 안 받습니다 — 대신
       **backend 가 실제로 발급한 키인지**를 봅니다. 이 검사가 없으면 아무나 임의
       경로로 서버 디스크를 채울 수 있습니다.
    """
    return await session.scalar(
        select(DogCard).where(DogCard.face_storage_key == storage_key)
    )


async def list_for_owner_for_update(
    session: AsyncSession, app_user_id: uuid.UUID
) -> list[DogCard]:
    """탈퇴가 지울 카드를 잠급니다. 얼굴 키를 읽어야 저장소를 정리할 수 있습니다."""
    stmt = (
        select(DogCard).where(DogCard.app_user_id == app_user_id).with_for_update()
    )
    return list(await session.scalars(stmt))


async def delete(session: AsyncSession, card: DogCard) -> None:
    await session.delete(card)


async def delete_all_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """탈퇴한 회원의 카드를 전부 지웁니다.

    ⚠️ **`app_users` CASCADE 에 기대면 안 됩니다.** 탈퇴는 그 행을 남기므로 CASCADE 가
       영영 안 돕니다 — 대화(chats)·피부 기록과 같은 이유입니다.
    """
    result = await session.execute(
        sql_delete(DogCard).where(DogCard.app_user_id == app_user_id)
    )
    return result.rowcount or 0
