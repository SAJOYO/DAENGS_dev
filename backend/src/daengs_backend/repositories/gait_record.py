"""gait_records DAO (D-043).

⚠️ **접근 판정은 모든 조회에 함께 들어갑니다.** `record_id` 만으로 찾는 함수를 일부러
   두지 않습니다 — 그런 함수가 있으면 언젠가 라우터가 그것을 쓰고, 남의 기록이
   보입니다. 없는 것과 남의 것은 같은 "못 찾음" 입니다 (`services/pet.py` 와 같은 규칙).

판정 사슬: record.pet_id → pets.id → 부른 사람. JOIN 하나로 끝나고 owner 를 중복
저장하지 않습니다.

**판정이 셋입니다** (docs/co-care.md §2, Task 19). 보행은 강아지의 건강 데이터라 **읽기는
구성원**(대표 ∪ 돌보미, `_accessible`)이고, **삭제는 대표만**(`_owned`)이고, **확정은
업로더 본인 또는 대표**(`_confirmable`)입니다. 바닥을 서로 합치지 마세요:
  - `_accessible` 을 삭제·확정에 쓰면 구성원 아무나 남의 기록을 지우거나 확정합니다.
  - `_owned` 를 확정에 그대로 두면(Task 19 이전 상태) **업로더가 자기가 막 올린
    영상을 확정 못 합니다** — 티켓 발급·PUT 은 구성원에게 열려 있는데 confirm 만
    대표로 막혀 있어 "반쯤 열린" 상태가 됩니다.

⚠ **새 기록을 만드는 것도 구성원(대표 ∪ 돌보미)입니다.** 그 게이트는 이 파일이 아니라
`services/gait.py::start_analysis` 의 `pet_repo.get_accessible` 입니다 (Task 12, 결정 ②
"돌보미는 기록하고 본다"). 처음에는 §2 에서 "생성은 대표만" 으로 미뤄 뒀지만 —
업로드 티켓 발급·PENDING 기록 생성은 저장소 객체를 만드는 것이라도 **강아지에
붙는 건강 데이터를 새로 시작하는 것**이지 지우거나 확정하는 것이 아니라서, 그 판단이
그대로 `_accessible` 바닥에 들어갑니다. 삭제(`soft_delete`)는 여전히 `_owned`(대표만)
입니다 — 열면 돌보미가 남의 집 보행 영상을 지웁니다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models.gait_record import GaitRecord
from daengs_backend.models.pet import Pet
from daengs_backend.repositories import pet as pet_repo


def _owned(app_user_id: uuid.UUID):
    """**대표만** + soft delete 필터. 삭제(`soft_delete`)의 바닥입니다."""
    return (
        select(GaitRecord)
        .join(Pet, Pet.id == GaitRecord.pet_id)
        .where(Pet.app_user_id == app_user_id, GaitRecord.deleted_at.is_(None))
    )


def _accessible(app_user_id: uuid.UUID):
    """**구성원(대표 ∪ 돌보미)** + soft delete 필터. 보기만 하는 조회의 바닥입니다.

    보행은 **강아지의 건강 데이터**라 돌보미도 봐야 합니다 (docs/co-care.md §2). 다만
    `_owned` 와 갈라 둔 것이 의도입니다 — `soft_delete`·`confirm_upload` 가 이 바닥을 쓰면
    **돌보미가 남의 집 보행 영상을 지웁니다.** 지우고 상태를 바꾸는 쪽은 `_owned` 그대로입니다.
    """
    return (
        select(GaitRecord)
        .join(Pet, Pet.id == GaitRecord.pet_id)
        .where(pet_repo.member_condition(app_user_id), GaitRecord.deleted_at.is_(None))
    )


def _confirmable(app_user_id: uuid.UUID):
    """**업로더 본인 또는 대표** + soft delete 필터. 확정(`confirm_upload`)의 바닥입니다.

    `care_repo.get_deletable` 과 같은 모양입니다 — 지울 자격이 있는 사람이 "적은 사람
    또는 대표" 둘인 것처럼, 확정할 자격이 있는 사람은 "올린 사람 또는 대표" 둘입니다.
    구성원 전체(`_accessible`)로 열면 다른 돌보미가 남의 업로드를 확정하고, 대표만
    (`_owned`)으로 두면 업로더 본인이 자기가 막 올린 영상을 확정 못 합니다(Task 19).

    `actor_app_user_id` 가 NULL 인 옛 기록(이 칸이 생기기 전)은 첫 조건이 항상 거짓이라
    자동으로 대표만 남습니다 — 예전 동작과 같습니다.
    """
    return (
        select(GaitRecord)
        .join(Pet, Pet.id == GaitRecord.pet_id)
        .where(
            or_(
                GaitRecord.actor_app_user_id == app_user_id,
                Pet.app_user_id == app_user_id,
            ),
            GaitRecord.deleted_at.is_(None),
        )
    )


async def get_owned(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    record_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> GaitRecord | None:
    """**대표만.** 삭제(`soft_delete`)가 이것을 씁니다. (확정은 `get_confirmable`.)"""
    stmt = _owned(app_user_id).where(GaitRecord.id == record_id)
    if for_update:
        stmt = stmt.with_for_update(of=GaitRecord)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_confirmable(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    record_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> GaitRecord | None:
    """**업로더 본인 또는 대표.** 확정(`confirm_upload`)이 이것을 씁니다 (Task 19)."""
    stmt = _confirmable(app_user_id).where(GaitRecord.id == record_id)
    if for_update:
        stmt = stmt.with_for_update(of=GaitRecord)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_accessible(
    session: AsyncSession, app_user_id: uuid.UUID, record_id: uuid.UUID
) -> GaitRecord | None:
    """**구성원이면** 한 건을 보여 줍니다. 읽기 전용이라 `for_update` 가 없습니다 —
    잠글 일이 있다는 것은 바꾼다는 뜻이고, 그건 `get_owned` 쪽입니다."""
    stmt = _accessible(app_user_id).where(GaitRecord.id == record_id)
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


async def get_accessible_pair(
    session: AsyncSession, app_user_id: uuid.UUID, ids: tuple[uuid.UUID, uuid.UUID]
) -> list[GaitRecord]:
    """비교할 두 기록을 **한 번에** 접근 판정과 함께 가져옵니다 (D-058).

    ⚠️ **두 건을 따로 조회하지 않습니다.** 하나씩 부르면 "첫 번째는 있고 두 번째는 없다"
       같은 중간 상태가 호출부로 새고, 그것을 어떻게 응답할지 판단이 두 곳으로 갈립니다.
       여기서는 **둘 다 닿을 수 있을 때만** 두 건을 돌려주고, 아니면 짧은 목록을 돌려줍니다 —
       호출부는 `len() != 2` 하나만 보면 됩니다.

    비교는 그리기만 하므로 판정이 **구성원** 기준입니다 (docs/co-care.md §2).

    같은 반려견인지까지는 보지 않습니다. 그건 접근 판정이 아니라 **비교의 규칙**이라
    서비스 계층이 판단합니다.
    """
    stmt = _accessible(app_user_id).where(GaitRecord.id.in_(ids))
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
        _accessible(app_user_id)
        .where(GaitRecord.pet_id == pet_id)
        .order_by(GaitRecord.created_at, GaitRecord.id)
        .limit(limit + 1)
    )
    if cursor is not None:
        anchor = await get_accessible(session, app_user_id, cursor)
        if anchor is None:
            # 지워졌거나 남의 것 — 조용히 처음부터 주지 않습니다. 호출부가 400 을 냅니다.
            raise LookupError("cursor")
        stmt = stmt.where(
            (GaitRecord.created_at, GaitRecord.id) > (anchor.created_at, anchor.id)
        )
    return list((await session.execute(stmt)).scalars())
