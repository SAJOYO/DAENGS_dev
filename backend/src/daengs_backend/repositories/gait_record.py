"""gait_records DAO (D-043).

⚠️ **소유권은 모든 조회에 함께 들어갑니다.** `record_id` 만으로 찾는 함수를 일부러
   두지 않습니다 — 그런 함수가 있으면 언젠가 라우터가 그것을 쓰고, 남의 기록이
   보입니다. 없는 것과 남의 것은 같은 "못 찾음" 입니다 (`services/pet.py` 와 같은 규칙).

소유권 사슬: record.pet_id → pets.id, pets.app_user_id → 부른 사람.
JOIN 하나로 끝나고 owner 를 중복 저장하지 않습니다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models.gait_record import GaitRecord
from daengs_backend.models.pet import Pet


def _owned(app_user_id: uuid.UUID):
    """소유권 + soft delete 필터. 모든 조회의 공통 바닥입니다."""
    return (
        select(GaitRecord)
        .join(Pet, Pet.id == GaitRecord.pet_id)
        .where(Pet.app_user_id == app_user_id, GaitRecord.deleted_at.is_(None))
    )


async def get_owned(
    session: AsyncSession, app_user_id: uuid.UUID, record_id: uuid.UUID
) -> GaitRecord | None:
    stmt = _owned(app_user_id).where(GaitRecord.id == record_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_by_storage_key(
    session: AsyncSession, storage_key: str, *, status: str | None = None
) -> GaitRecord | None:
    """저장 키로 기록을 찾습니다 — **임시 LocalBridge 전용** (D-043).

    ⚠️ **소유권 JOIN 이 없는 유일한 조회입니다. 위 규칙의 의도된 예외입니다.**
       bridge 는 GCS Signed URL 을 흉내 내는 자리라 인증 헤더를 받지 않습니다
       (받게 하면 GCS 로 바꿀 때 앱 코드가 또 바뀝니다). 대신 **키 자체가 자격**입니다:
       키는 backend 가 만든 uuid4 라 추측할 수 없고, 발급받은 사람은 소유자뿐입니다.

       이 함수의 진짜 목적은 **임의 경로 쓰기를 막는 것**입니다. 이게 없으면 bridge 가
       아무 경로나 받아 줘서, 인증 없이 서버 디스크를 채울 수 있습니다.
       `status="PENDING"` 을 주면 티켓이 아직 살아 있는 것에만 씁니다 — confirm 뒤에
       같은 키로 덮어쓰는 것도 막힙니다.

    GCS 로 넘어가면 bridge 와 함께 사라질 함수입니다.
    """
    stmt = select(GaitRecord).where(
        GaitRecord.original_storage_key == storage_key,
        GaitRecord.deleted_at.is_(None),
    )
    if status is not None:
        stmt = stmt.where(GaitRecord.status == status)
    return (await session.execute(stmt)).scalars().first()


async def list_for_pet(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    *,
    limit: int,
    cursor: uuid.UUID | None,
) -> list[GaitRecord]:
    """한 강아지의 기록, 오래된 것부터. limit+1 개를 돌려주면 호출부가 next_cursor 를
    만듭니다 (한 번 더 세는 쿼리를 아낍니다).

    정렬 키에 id 를 tiebreaker 로 넣습니다 — created_at 이 같으면 커서가 항목을
    건너뛰거나 두 번 냅니다 (/v1 목록에서 이미 밟았던 함정).
    """
    stmt = (
        _owned(app_user_id)
        .where(GaitRecord.pet_id == pet_id)
        .order_by(GaitRecord.created_at, GaitRecord.id)
        .limit(limit + 1)
    )
    if cursor is not None:
        anchor = await get_owned(session, app_user_id, cursor)
        if anchor is None:
            # 지워졌거나 남의 것 — 조용히 처음부터 주지 않습니다. 호출부가 400 을 냅니다.
            raise LookupError("cursor")
        stmt = stmt.where(
            (GaitRecord.created_at, GaitRecord.id) > (anchor.created_at, anchor.id)
        )
    return list((await session.execute(stmt)).scalars())
