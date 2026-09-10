"""R — 피부 변화 기록 쿼리. 판단은 `services/screening.py` 가 합니다.

**바닥이 둘입니다** (docs/co-care.md §2, Task 14). `gait_record.py` 와 같은 모양입니다 —
`_owned`(창작자만)와 `_accessible`(강아지의 구성원)로 갈립니다. 그런데 여기는 `gait_record.py`
처럼 조인 하나로 못 씁니다: `ScreeningRecord.pet_id` 가 **nullable** 이라(아이를 안 고르고
찍은 개인 기록이 있습니다) 소유가 강아지에서 유도되지 않습니다 — 소유는 여전히
`ScreeningRecord.app_user_id`(만든 사람)에 직접 저장됩니다.

그래서 `_accessible` 은 "내가 구성원인 강아지 id 집합" 을 서브쿼리로 뽑아 `pet_id IN (...)`
으로 겁니다. `pet_id IS NULL` 인 행은 그 어떤 `IN` 에도 안 걸리므로 — 만든 사람이 아니면
**개인 기록은 자동으로 안 보입니다.** 이것이 생성을 구성원으로 열어도 안전한 이유입니다:
강아지에 붙은 기록만 그 집의 것이 되고, 안 붙은 기록은 여전히 그 사람만의 것입니다.

지우기(`get_deletable`)는 다시 다릅니다 — **창작자 또는 그 아이의 대표**(`care_repo.get_deletable`
과 같은 모양)이지 구성원 전체가 아닙니다. 볼 수 있는 사람 전체에 지우기를 열면 돌보미끼리
서로의 기록을 지웁니다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete as sql_delete
from sqlalchemy import or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Pet, ScreeningRecord
from daengs_backend.repositories import pet as pet_repo


def add(session: AsyncSession, record: ScreeningRecord) -> ScreeningRecord:
    session.add(record)
    return record


async def get_owned(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    record_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> ScreeningRecord | None:
    """**내 것일 때만** 돌려줍니다.

    PK 로만 찾으면 남의 record id 를 넣어 남의 피부 사진을 볼 수 있습니다. 소유자
    조건을 이 함수 안에 묶어 둬서 부르는 쪽이 잊을 자리를 없앱니다 (pet 과 같은 규칙).
    """
    stmt = select(ScreeningRecord).where(
        ScreeningRecord.id == record_id,
        ScreeningRecord.app_user_id == app_user_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def list_for_owner(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    *,
    pet_id: uuid.UUID | None = None,
    before: ScreeningRecord | None = None,
    limit: int = 50,
) -> list[ScreeningRecord]:
    """**최근 순.** 변화 기록은 늘 최근 것을 위에 놓고 봅니다.

    `pet_id` 를 주면 그 아이 것만 봅니다. **`None` 은 "전부"이지 "아이 없는 것"이
    아닙니다** — 아이를 안 고르고 찍은 기록만 보고 싶은 화면은 아직 없습니다.

    `before` 를 주면 **그 기록보다 앞선 것만** 봅니다. 이것을 부르는 쪽에서 걸러 낼 수 없는
    이유는 `limit` 이 **거르기 전에** 걸리기 때문입니다 — 기준 기록이 최신 N건 밖이면 창 안에
    나중 기록만 들어와, 부르는 쪽에서 아무리 걸러도 이력이 빈 채로 나옵니다 (#79 3번 리뷰).
    같은 `created_at` 은 `id` 로 가릅니다. 시각이 같은 두 행의 순서가 실행마다 달라지면 같은
    질문이 다른 이력을 받습니다.
    """
    stmt = select(ScreeningRecord).where(ScreeningRecord.app_user_id == app_user_id)
    if pet_id is not None:
        stmt = stmt.where(ScreeningRecord.pet_id == pet_id)
    if before is not None:
        stmt = stmt.where(
            tuple_(ScreeningRecord.created_at, ScreeningRecord.id)
            < tuple_(before.created_at, before.id)
        )
    stmt = stmt.order_by(ScreeningRecord.created_at.desc(), ScreeningRecord.id.desc())
    return list(await session.scalars(stmt.limit(limit)))


def _accessible_pet_ids(app_user_id: uuid.UUID):
    """내가 구성원(대표 ∪ 돌보미)인 강아지 id 들 — `_accessible` 의 서브쿼리입니다.

    `pet_repo.member_condition` 을 조인 없이 그대로 쓰지 못하는 이유가 위 모듈
    독스트링의 nullable `pet_id` 사정입니다. `Pet` 을 조인하면 개인 기록(=NULL)이
    outer join 을 거치며 다뤄야 할 것이 늘어나는데, 서브쿼리 `IN` 은 `pet_id IS NULL`
    을 아무 추가 조건 없이 자동으로 걸러 줍니다 — `NULL IN (...)` 은 참이 아닙니다.
    """
    return select(Pet.id).where(pet_repo.member_condition(app_user_id))


async def get_accessible(
    session: AsyncSession, app_user_id: uuid.UUID, record_id: uuid.UUID
) -> ScreeningRecord | None:
    """**창작자이거나, 강아지에 붙었고 내가 그 아이의 구성원이면** 돌려줍니다.

    읽기 전용이라 `for_update` 가 없습니다 — 잠글 일이 있다는 것은 바꾼다는 뜻이고,
    그건 `get_owned`(확정)나 `get_deletable`(삭제) 쪽입니다.
    """
    stmt = select(ScreeningRecord).where(
        ScreeningRecord.id == record_id,
        or_(
            ScreeningRecord.app_user_id == app_user_id,
            ScreeningRecord.pet_id.in_(_accessible_pet_ids(app_user_id)),
        ),
    )
    return await session.scalar(stmt)


async def list_accessible(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    *,
    pet_id: uuid.UUID | None = None,
    before: ScreeningRecord | None = None,
    limit: int = 50,
) -> list[ScreeningRecord]:
    """**내 기록 + 내가 구성원인 강아지의 기록**, 최근 순. `list_for_owner` 의 구성원판입니다.

    페이지네이션 규칙(`before`·정렬·tiebreaker)은 `list_for_owner` 와 같습니다 — 둘을
    갈라 둔 것은 판정 바닥 때문이지 목록을 넘기는 방식이 달라서가 아닙니다.
    """
    stmt = select(ScreeningRecord).where(
        or_(
            ScreeningRecord.app_user_id == app_user_id,
            ScreeningRecord.pet_id.in_(_accessible_pet_ids(app_user_id)),
        )
    )
    if pet_id is not None:
        stmt = stmt.where(ScreeningRecord.pet_id == pet_id)
    if before is not None:
        stmt = stmt.where(
            tuple_(ScreeningRecord.created_at, ScreeningRecord.id)
            < tuple_(before.created_at, before.id)
        )
    stmt = stmt.order_by(ScreeningRecord.created_at.desc(), ScreeningRecord.id.desc())
    return list(await session.scalars(stmt.limit(limit)))


async def get_deletable(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    record_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> ScreeningRecord | None:
    """**창작자 또는 그 아이의 대표**일 때만 돌려줍니다 (docs/co-care.md §2).

    `care_repo.get_deletable` 과 같은 모양입니다 — 기록을 볼 수 있는 사람 전체(구성원)
    가 아니라 지울 자격이 있는 **둘**로 좁힙니다. 구성원 전체에 열면 돌보미끼리 서로의
    기록을 지우고, `app_user_id`(창작자)만 보면 대표가 돌보미의 오촬영을 영영 못
    지웁니다.

    `pet_id IS NULL` 인 개인 기록은 outer join 이 `Pet` 행을 못 찾아 `Pet.app_user_id`
    가 NULL 이 되므로 둘째 조건이 자동으로 거짓입니다 — **창작자만** 지웁니다. 대표라는
    개념 자체가 없는 기록이니 따로 갈라 쓸 필요가 없습니다.
    """
    stmt = (
        select(ScreeningRecord)
        .outerjoin(Pet, Pet.id == ScreeningRecord.pet_id)
        .where(
            ScreeningRecord.id == record_id,
            or_(
                ScreeningRecord.app_user_id == app_user_id,
                Pet.app_user_id == app_user_id,
            ),
        )
    )
    if for_update:
        stmt = stmt.with_for_update(of=ScreeningRecord)
    return await session.scalar(stmt)


async def find_by_storage_key(
    session: AsyncSession, storage_key: str, *, status: str | None = None
) -> ScreeningRecord | None:
    """저장소 키 하나로 행을 찾습니다. **bridge 전용입니다.**

    ⚠️ **소유자 조건이 없습니다.** bridge 는 인증 헤더를 안 받습니다 — Signed URL 을
       흉내 내는 자리라, 헤더를 요구하면 저장소를 GCS 로 바꿀 때 앱 코드가 또
       바뀝니다. 대신 **backend 가 실제로 발급한 키인지**를 여기서 봅니다. 이 검사가
       없으면 아무나 임의 경로로 서버 디스크를 채울 수 있습니다.

    `status` 로 좁히면 확정된 기록에 덮어쓰는 것을 막습니다.
    """
    stmt = select(ScreeningRecord).where(ScreeningRecord.photo_storage_key == storage_key)
    if status is not None:
        stmt = stmt.where(ScreeningRecord.status == status)
    return await session.scalar(stmt)


async def list_for_owner_for_update(
    session: AsyncSession, app_user_id: uuid.UUID
) -> list[ScreeningRecord]:
    """탈퇴가 지울 기록을 잠급니다. 사진 키를 읽어야 저장소를 정리할 수 있습니다."""
    stmt = (
        select(ScreeningRecord)
        .where(ScreeningRecord.app_user_id == app_user_id)
        .with_for_update()
    )
    return list(await session.scalars(stmt))


async def delete(session: AsyncSession, record: ScreeningRecord) -> None:
    await session.delete(record)


async def delete_all_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """탈퇴한 회원의 기록을 전부 지웁니다.

    ⚠️ **`app_users` CASCADE 에 기대면 안 됩니다.** 탈퇴는 그 행을 남기므로
       (kakao_id 로 "이미 탈퇴한 사람" 을 알아보려고) CASCADE 가 영영 안 돕니다 —
       대화(chats)가 같은 이유로 명시 삭제인 것과 같습니다.
    """
    result = await session.execute(
        sql_delete(ScreeningRecord).where(ScreeningRecord.app_user_id == app_user_id)
    )
    return result.rowcount or 0
