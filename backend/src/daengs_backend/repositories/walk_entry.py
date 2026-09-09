"""산책 기록 조회. 소유권 필터와 행 잠금을 여기서 적용한다."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from daengs_backend.models import Pet, Walk, WalkPet
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.schemas.walk_entry import RecordProfileQuery


async def owned_walk(session: AsyncSession, owner: uuid.UUID, walk_id: uuid.UUID, *, lock=False):
    query = (
        select(Walk)
        .where(Walk.id == walk_id, Walk.app_user_id == owner)
        .options(selectinload(Walk.pets))
    )
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


async def entries(session: AsyncSession, walk_ids: list[uuid.UUID]):
    return list(
        await session.scalars(
            select(WalkEntry)
            .where(WalkEntry.walk_id.in_(walk_ids))
            .order_by(WalkEntry.walk_id, WalkEntry.id)
        )
    )


async def get_entry(session: AsyncSession, walk_id: uuid.UUID, entry_id: uuid.UUID):
    return await session.get(WalkEntry, (walk_id, entry_id))


async def pet_is_accessible(session: AsyncSession, member: uuid.UUID, pet_id: uuid.UUID):
    """그 아이를 산책 기록에 붙일 수 있는가 — **구성원(대표 ∪ 돌보미)이면 됩니다**
    (docs/co-care.md §2).

    **산책 자체의 소유는 안 옮깁니다** — 여기 걸린 판정은 "동행한 아이를 고를 수 있나" 뿐이고,
    어느 산책을 고칠 수 있는지는 위 `owned_walk` 가 `Walk.app_user_id` 로 계속 봅니다.
    """
    return await session.scalar(
        select(Pet.id).where(Pet.id == pet_id, pet_repo._is_member(member))
    )


async def profile_walks(session: AsyncSession, owner: uuid.UUID, spec: RecordProfileQuery):
    query = (
        select(Walk)
        .join(WalkPet)
        .where(Walk.app_user_id == owner, WalkPet.pet_id == spec.pet_id)
        .order_by(Walk.started_at, Walk.id)
    )
    if spec.since:
        query = query.where(Walk.started_at >= spec.since)
    if spec.until:
        query = query.where(Walk.started_at < spec.until)
    return list(await session.scalars(query))
