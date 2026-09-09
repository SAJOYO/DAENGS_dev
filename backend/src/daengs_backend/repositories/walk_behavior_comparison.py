"""선택된 산책 안의 현재 행동 기록만 제한된 수로 읽습니다. commit하지 않습니다."""

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Walk, WalkPet
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.models.walk_entry_v2 import WalkEntryPin


@dataclass(frozen=True)
class BehaviorEntryRow:
    entry: WalkEntry
    pin: WalkEntryPin | None
    client_session_id: uuid.UUID | None


def behavior_entries_statement(
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    walk_ids: tuple[uuid.UUID, ...],
    behavior_code: str,
    *,
    limit: int,
) -> Select:
    return (
        select(WalkEntry, WalkEntryPin, Walk.client_session_id)
        .join(Walk, Walk.id == WalkEntry.walk_id)
        .join(WalkPet, WalkPet.walk_id == Walk.id)
        .outerjoin(
            WalkEntryPin,
            (WalkEntryPin.walk_id == WalkEntry.walk_id) & (WalkEntryPin.entry_id == WalkEntry.id),
        )
        .where(
            Walk.app_user_id == app_user_id,
            WalkPet.pet_id == pet_id,
            WalkEntry.walk_id.in_(walk_ids),
            WalkEntry.payload.is_not(None),
            WalkEntry.payload != JSONB.NULL,
            WalkEntry.payload["kind"].astext == "behavior",
            WalkEntry.payload["pet_id"].astext == str(pet_id),
            WalkEntry.payload["behavior_code"].astext == behavior_code,
        )
        .order_by(WalkEntry.walk_id, WalkEntry.id)
        .limit(limit)
    )


async def list_behavior_entries(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    walk_ids: tuple[uuid.UUID, ...],
    behavior_code: str,
    *,
    limit: int,
) -> list[BehaviorEntryRow]:
    if not walk_ids:
        return []
    rows = await session.execute(
        behavior_entries_statement(app_user_id, pet_id, walk_ids, behavior_code, limit=limit),
    )
    return [BehaviorEntryRow(*row) for row in rows.all()]
