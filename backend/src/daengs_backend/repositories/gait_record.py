"""gait_records DAO (D-043).

⚠️ **소유권은 모든 조회에 함께 들어갑니다.** `record_id` 만으로 찾는 함수를 일부러
   두지 않습니다 — 그런 함수가 있으면 언젠가 라우터가 그것을 쓰고, 남의 기록이
   보입니다. 없는 것과 남의 것은 같은 "못 찾음" 입니다 (`services/pet.py` 와 같은 규칙).

소유권 사슬: record.pet_id → pets.id, pets.app_user_id → 부른 사람.
JOIN 하나로 끝나고 owner 를 중복 저장하지 않습니다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
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
    session: AsyncSession,
    app_user_id: uuid.UUID,
    record_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> GaitRecord | None:
    stmt = _owned(app_user_id).where(GaitRecord.id == record_id)
    if for_update:
        stmt = stmt.with_for_update(of=GaitRecord)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_for_pets_for_update(
    session: AsyncSession, pet_ids: list[uuid.UUID]
) -> list[GaitRecord]:
    """반려견 삭제 전에 정리할 모든 기록을 잠급니다 (soft-deleted 행 포함).

    소유권은 호출자가 잠근 pets 행으로 이미 확인했습니다. deleted_at 으로 거르지 않는
    이유는 이전 cleanup 실패 뒤 남은 행도 이번 재시도에서 반드시 정리해야 해서입니다.
    """
    if not pet_ids:
        return []
    stmt = (
        select(GaitRecord)
        .where(GaitRecord.pet_id.in_(pet_ids))
        .order_by(GaitRecord.id)
        .with_for_update()
    )
    return list((await session.execute(stmt)).scalars())


async def find_by_storage_key(
    session: AsyncSession,
    storage_key: str,
    *,
    status: str | None = None,
    allow_overlay: bool = False,
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

    ⚠️ **`allow_overlay` 는 다운로드에만 켭니다.** 업로드는 원본 키만 받아야 합니다 —
       overlay 키까지 열어 주면 앱이 **분석 결과 영상을 덮어쓸 수 있습니다.** overlay 를
       만드는 것은 워커뿐이고, 워커는 이 bridge 를 지나지 않습니다(같은 볼륨에 직접 씁니다).

       기본값이 False 인 이유가 그것입니다. 처음에는 원본만 보게 짰다가 **overlay 를 아예
       못 꺼내는 버그**가 됐습니다 — 실기기 검증에서 overlay 다운로드가 404 로 잡혔습니다
       (2026-09-02). 그때 "그럼 둘 다 열자"로 가면 위 위험이 열립니다.

    GCS 로 넘어가면 bridge 와 함께 사라질 함수입니다.
    """
    key_match = GaitRecord.original_storage_key == storage_key
    if allow_overlay:
        key_match = or_(key_match, GaitRecord.overlay_storage_key == storage_key)
    stmt = select(GaitRecord).where(key_match, GaitRecord.deleted_at.is_(None))
    if status is not None:
        stmt = stmt.where(GaitRecord.status == status)
    return (await session.execute(stmt)).scalars().first()


async def get_owned_pair(
    session: AsyncSession, app_user_id: uuid.UUID, ids: tuple[uuid.UUID, uuid.UUID]
) -> list[GaitRecord]:
    """비교할 두 기록을 **한 번에** 소유권과 함께 가져옵니다 (D-057).

    ⚠️ **두 건을 따로 조회하지 않습니다.** 하나씩 부르면 "첫 번째는 있고 두 번째는 없다"
       같은 중간 상태가 호출부로 새고, 그것을 어떻게 응답할지 판단이 두 곳으로 갈립니다.
       여기서는 **둘 다 내 것일 때만** 두 건을 돌려주고, 아니면 짧은 목록을 돌려줍니다 —
       호출부는 `len() != 2` 하나만 보면 됩니다.

    같은 반려견인지까지는 보지 않습니다. 그건 소유권이 아니라 **비교의 규칙**이라
    서비스 계층이 판단합니다.
    """
    stmt = _owned(app_user_id).where(GaitRecord.id.in_(ids))
    return list((await session.execute(stmt)).scalars())


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
