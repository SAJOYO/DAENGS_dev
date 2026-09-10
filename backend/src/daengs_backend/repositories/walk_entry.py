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
        select(Pet.id).where(Pet.id == pet_id, pet_repo.member_condition(member))
    )


async def profile_walks(
    session: AsyncSession, _app_user_id: uuid.UUID, spec: RecordProfileQuery
):
    """그 아이가 나간 산책 전부 — **소유자 조건을 걸지 않습니다**
    (`walk.count_for_pet_between` 과 같은 판단, docs/co-care.md §2).

    부르는 쪽(`services/walk_entry.py::profile`)이 이미 `pet_is_accessible` 로 구성원인지를
    확인한 뒤라, 여기서 다시 사람으로 거르면 **돌보미가 200 을 받으면서 내용은 빈**
    모양이 됩니다 — 예전에는 게이트가 대표만이라 404 였으므로, 게이트만 열고 이곳을
    그대로 두면 "기록이 없다" 로 조용히 바뀌는 것이 더 나쁘습니다.

    프로필은 **강아지의 행동 요약**이지 사람의 성과가 아닙니다 (스펙 결정 ①) —
    아빠가 아침에 본 배변도 그 아이의 기록입니다. 산책의 **소유는 그대로 올린 사람
    것**이고(`owned_walk` 가 계속 `Walk.app_user_id` 를 봅니다), 여기서 여는 것은 읽기뿐입니다.

    인자는 부르는 쪽을 안 고치려고 시그니처에만 남겨 둡니다 — `count_for_pet_between` 과 같습니다.
    """
    query = (
        select(Walk)
        .join(WalkPet)
        .where(WalkPet.pet_id == spec.pet_id)
        .order_by(Walk.started_at, Walk.id)
    )
    if spec.since:
        query = query.where(Walk.started_at >= spec.since)
    if spec.until:
        query = query.where(Walk.started_at < spec.until)
    return list(await session.scalars(query))
