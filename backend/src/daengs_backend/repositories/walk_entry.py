"""산책 기록 조회. 소유권 필터와 행 잠금을 여기서 적용한다."""

import uuid
from collections.abc import Sequence

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


async def pet_group_ids(session: AsyncSession, pet_id: uuid.UUID) -> list[uuid.UUID]:
    """그 아이와 **같은 실제 강아지**인 pet id 전부 (MVP 결정 §7).

    연결 안 된 아이는 `[pet_id]` 하나라 지금까지와 같습니다.

    ⚠️ **권한을 안 봅니다** — 부르는 쪽(`services/walk_entry.py::profile`)이 바로 앞에서
    `pet_is_accessible` 로 확인한 뒤입니다. 이 함수만 따로 부르지 마세요.

    같은 층에 두는 이유는 `profile` 이 쓰는 조회를 한 모듈에 모아 두기 위해서입니다 —
    테스트가 이 모듈을 통째로 대역으로 바꿉니다.
    """
    group = select(Pet.identity_id).where(
        Pet.id == pet_id, Pet.identity_id.is_not(None)
    )
    rows = list(
        await session.scalars(
            select(Pet.id).where(Pet.identity_id.in_(group)).order_by(Pet.id)
        )
    )
    return rows or [pet_id]


async def profile_walks(
    session: AsyncSession,
    _app_user_id: uuid.UUID,
    spec: RecordProfileQuery,
    pet_ids: Sequence[uuid.UUID] | None = None,
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
    # `pet_ids` 는 **논리 연결**된 그룹 전체입니다 (MVP 결정 §7). 안 주면 그 아이 하나라
    # 지금까지와 같습니다.
    #
    # ⚠️ **`distinct()` 가 없으면 안 됩니다.** 그룹의 두 아이가 같은 산책에 태그돼 있으면
    #    조인이 그 산책을 두 줄로 냅니다 — 한 마리는 절대 못 만드는 상황이라 지금까지
    #    없어도 됐던 것입니다.
    query = (
        select(Walk)
        .join(WalkPet)
        .where(WalkPet.pet_id.in_(set(pet_ids or [spec.pet_id])))
        .order_by(Walk.started_at, Walk.id)
        .distinct()
    )
    if spec.since:
        query = query.where(Walk.started_at >= spec.since)
    if spec.until:
        query = query.where(Walk.started_at < spec.until)
    return list(await session.scalars(query))
