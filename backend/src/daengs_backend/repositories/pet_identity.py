"""pet_identities 조회·저장. 쿼리만 있고 판단은 없습니다.

"어느 행을 카드로 고르나"·"누가 그룹 주보호자인가"·"연결해도 되나" 는
`services/pet_identity.py` 가 정합니다. commit 도 하지 않습니다.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Pet, PetIdentity, PetMember

__all__ = [
    "add",
    "count_pets",
    "delete",
    "get_many",
    "guardian_ids",
    "pet_ids_for",
    "pet_of_user",
    "pets_for",
]


def add(session: AsyncSession, owner_pet_id: uuid.UUID) -> PetIdentity:
    """새 그룹. **앵커는 초대한 쪽의 pet 행입니다** — 그 행의 대표가 곧 그룹 주보호자입니다.

    부르는 쪽이 `pets.identity_id` 를 같이 채워야 합니다. 앵커 행조차 이 값이 비어 있으면
    `pets_for` 가 그 행을 못 찾아 그룹이 반쪽이 됩니다 (서비스가 한 트랜잭션에서 둘 다 합니다).
    """
    row = PetIdentity(owner_pet_id=owner_pet_id)
    session.add(row)
    return row


async def get_many(
    session: AsyncSession, identity_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, PetIdentity]:
    """id → 그룹. 목록 한 번에 여러 그룹의 앵커를 알아야 해서 한 번에 받습니다.

    빈 목록이면 쿼리도 안 날립니다 — `IN ()` 은 DB 마다 다르게 굽니다.
    """
    if not identity_ids:
        return {}
    stmt = select(PetIdentity).where(PetIdentity.id.in_(set(identity_ids)))
    return {row.id: row for row in await session.scalars(stmt)}


async def pets_for(session: AsyncSession, identity_id: uuid.UUID) -> list[Pet]:
    """그 그룹의 pet 행 전부, 등록 순서대로."""
    stmt = (
        select(Pet)
        .where(Pet.identity_id == identity_id)
        .order_by(Pet.created_at, Pet.id)
    )
    return list(await session.scalars(stmt))


async def pet_ids_for(session: AsyncSession, identity_id: uuid.UUID) -> list[uuid.UUID]:
    """그 그룹의 pet id 전부. 케어·산책 공동 조회의 `IN` 목록이 이것입니다."""
    stmt = (
        select(Pet.id)
        .where(Pet.identity_id == identity_id)
        .order_by(Pet.created_at, Pet.id)
    )
    return list(await session.scalars(stmt))


async def count_pets(session: AsyncSession, identity_id: uuid.UUID) -> int:
    """그룹에 남은 pet 행 수. 0 이나 1 이면 그룹이 더 이상 뜻이 없습니다 —
    `services/pet_identity.py::prune` 이 그때 그룹 행을 지웁니다."""
    stmt = (
        select(func.count())
        .select_from(Pet)
        .where(Pet.identity_id == identity_id)
    )
    return int(await session.scalar(stmt) or 0)


async def guardian_ids(session: AsyncSession, identity_id: uuid.UUID) -> set[uuid.UUID]:
    """그룹 전체의 보호자 **중복 제거된** 사용자 id (대표 ∪ 돌보미).

    보호자 상한(`MAX_MEMBERS_PER_PET`)이 이것을 봅니다 — pet 행별로 세면 연결할 때마다
    그룹 인원이 상한을 넘어 늘어납니다 (MVP 결정 §4). 한 사람이 그룹 안에서 대표이면서
    다른 행의 돌보미일 수 있으므로 **집합**으로 돌려줍니다.
    """
    owners = select(Pet.app_user_id).where(Pet.identity_id == identity_id)
    carers = (
        select(PetMember.app_user_id)
        .join(Pet, Pet.id == PetMember.pet_id)
        .where(Pet.identity_id == identity_id)
    )
    return set(await session.scalars(owners.union(carers)))


async def pet_of_user(
    session: AsyncSession, identity_id: uuid.UUID, app_user_id: uuid.UUID
) -> Pet | None:
    """그 그룹 안에서 **그 사람이 대표인 행**. 없으면 None.

    `pets_identity_one_per_user` 부분 UNIQUE 가 "한 사람은 한 그룹에 행 하나" 를 보장하므로
    많아야 하나입니다. 승계가 이것을 봅니다 — 대상이 이미 자기 행을 갖고 있는데 앵커 행의
    소유까지 넘기면 그 UNIQUE 를 위반해 500 이 납니다.
    """
    stmt = select(Pet).where(
        Pet.identity_id == identity_id, Pet.app_user_id == app_user_id
    )
    return await session.scalar(stmt)


async def delete(session: AsyncSession, identity_id: uuid.UUID) -> int:
    """그룹 행을 지웁니다. `pets.identity_id` 의 `ON DELETE SET NULL` 이 남은 행을
    독립 강아지로 되돌립니다 — **pet 행도 기록도 안 지워집니다.**"""
    result = await session.execute(
        sql_delete(PetIdentity).where(PetIdentity.id == identity_id)
    )
    return int(result.rowcount or 0)
