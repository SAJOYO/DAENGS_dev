"""R — 피부 변화 기록 쿼리. 판단은 `services/screening.py` 가 합니다."""

from __future__ import annotations

import uuid

from sqlalchemy import delete as sql_delete
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import ScreeningRecord


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
