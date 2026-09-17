"""R — 콘솔이 뽑은 도감 카드 쿼리 (#592). 판단은 `services/admin_card_store.py` 가 합니다.

`ai_card.py` 와 달리 **소유자 조건이 없습니다** — 콘솔 권한을 가진 관리자 전원이 같은 목록을
보고 서로의 카드를 지울 수 있습니다(사용자 결정 09-18). 그래서 `get` 도 `admin_user_id` 를
받지 않습니다. 권한은 라우터의 `Perm.SEARCH_INSPECT` 하나가 봅니다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import AdminAiCard


def add(session: AsyncSession, card: AdminAiCard) -> AdminAiCard:
    session.add(card)
    return card


async def get(session: AsyncSession, card_id: uuid.UUID, *, for_update: bool = False) -> AdminAiCard | None:
    stmt = select(AdminAiCard).where(AdminAiCard.id == card_id)
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def list_recent(session: AsyncSession, *, limit: int = 50) -> list[AdminAiCard]:
    """최근 것부터. **관리자 전원의 카드**입니다 — `idx_admin_ai_cards_created` 가 이 정렬을 받칩니다."""
    stmt = select(AdminAiCard).order_by(AdminAiCard.created_at.desc()).limit(limit)
    return list(await session.scalars(stmt))


async def delete(session: AsyncSession, card: AdminAiCard) -> None:
    await session.delete(card)
