"""pets 조회·저장. 쿼리만 있고 판단은 없습니다.

"몇 마리까지 되나"·"대표를 누구로 승계하나"는 services 가 정합니다.
commit 도 하지 않습니다 — 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Pet

__all__ = [
    "add",
    "count_for_owner",
    "delete",
    "get_owned",
    "list_for_owner",
    "owned_ids",
]


async def list_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> list[Pet]:
    """내 강아지 전부, **등록 순서대로.**

    순서가 곧 앱의 카드 순서이고, 대표를 지웠을 때 승계 대상도 이 목록의 첫
    번째입니다. `created_at` 이 같을 수 있어(같은 초에 둘을 넣으면) `id` 로 한 번
    더 정렬합니다 — 안 하면 순서가 호출마다 뒤집힐 수 있습니다.
    """
    stmt = (
        select(Pet)
        .where(Pet.app_user_id == app_user_id)
        .order_by(Pet.created_at, Pet.id)
    )
    return list(await session.scalars(stmt))


async def get_owned(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> Pet | None:
    """**내 것일 때만** 돌려줍니다.

    PK 로만 찾으면 남의 강아지 id 를 넣어 남의 정보를 읽거나 지울 수 있습니다.
    소유자 조건을 이 함수 안에 묶어 둬서, 부르는 쪽이 잊을 자리를 없앱니다.
    """
    stmt = select(Pet).where(Pet.id == pet_id, Pet.app_user_id == app_user_id)
    return await session.scalar(stmt)


async def owned_ids(
    session: AsyncSession, app_user_id: uuid.UUID, pet_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """주어진 id 중 **내 강아지인 것**만.

    한 마리씩 `get_owned` 를 부르면 마릿수만큼 왕복합니다. 산책 하나를 올릴 때마다
    그러면 아깝습니다.

    빈 목록이면 쿼리도 안 날립니다 — `IN ()` 은 DB 마다 다르게 굽니다.
    """
    if not pet_ids:
        return set()
    stmt = select(Pet.id).where(Pet.app_user_id == app_user_id, Pet.id.in_(pet_ids))
    return set(await session.scalars(stmt))


async def count_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """마릿수. 상한 검사에 씁니다."""
    stmt = select(Pet.id).where(Pet.app_user_id == app_user_id)
    return len(list(await session.scalars(stmt)))


def add(session: AsyncSession, pet: Pet) -> Pet:
    session.add(pet)
    return pet


async def delete(session: AsyncSession, pet: Pet) -> None:
    await session.delete(pet)


async def delete_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """한 회원의 강아지를 **전부** 지웁니다. 탈퇴에서 부릅니다.

    산책과의 조인 행(`walk_pets`)은 DB 의 `ON DELETE CASCADE` 가 따라 지웁니다.
    산책 자체는 여기서 안 건드립니다 — 그건 walk 리포지토리의 몫입니다.

    :returns: 지운 마릿수.
    """
    result = await session.execute(delete(Pet).where(Pet.app_user_id == app_user_id))
    return result.rowcount or 0
