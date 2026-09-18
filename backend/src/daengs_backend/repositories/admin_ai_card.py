"""R — 콘솔이 뽑은 도감 카드 쿼리 (#592). 판단은 `services/admin_card_store.py` 가 합니다.

`ai_card.py` 와 달리 **소유자 조건이 없습니다** — 콘솔 권한을 가진 관리자 전원이 같은 목록을
보고 서로의 카드를 지울 수 있습니다(사용자 결정 09-18). 그래서 `get` 도 `admin_user_id` 를
받지 않습니다. 권한은 라우터의 `Perm.SEARCH_INSPECT` 하나가 봅니다.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select, tuple_
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


async def list_recent(
    session: AsyncSession,
    *,
    limit: int = 10,
    before: tuple[datetime.datetime, uuid.UUID] | None = None,
) -> list[AdminAiCard]:
    """최근 것부터 한 쪽. **관리자 전원의 카드**입니다 — `idx_admin_ai_cards_created` 가 이 정렬을 받칩니다.

    **키셋입니다 — OFFSET 이 아닙니다** (`admin_audit_log.py`·`app_user.py` 와 같은 규칙).
    이 표는 **읽는 동안에도 늡니다** — 관리자가 목록을 열어 둔 채 옆에서 카드를 뽑으면
    새 행이 맨 앞에 끼어들어서, OFFSET 이면 다음 쪽이 한 줄을 건너뛰거나 이미 본 줄을 다시
    보여 줍니다. `before` 는 마지막으로 본 행의 `(created_at, id)` 입니다 — 같은 시각에
    저장된 행이 있을 수 있어 `id` 로 한 번 더 가릅니다(튜플 비교라 인덱스가 그대로 듣습니다).
    """
    stmt = select(AdminAiCard).order_by(AdminAiCard.created_at.desc(), AdminAiCard.id.desc())
    if before is not None:
        at, last_id = before
        stmt = stmt.where(tuple_(AdminAiCard.created_at, AdminAiCard.id) < tuple_(at, last_id))
    return list(await session.scalars(stmt.limit(limit)))


async def delete(session: AsyncSession, card: AdminAiCard) -> None:
    await session.delete(card)
