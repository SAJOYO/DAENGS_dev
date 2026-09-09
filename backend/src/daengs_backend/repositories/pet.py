"""pets 조회·저장. 쿼리만 있고 판단은 없습니다.

"몇 마리까지 되나"·"대표를 누구로 승계하나"는 services 가 정합니다.
commit 도 하지 않습니다 — 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Pet, PetMember

__all__ = [
    "accessible_ids",
    "add",
    "count_accessible",
    "count_by_owners",
    "count_for_owner",
    "delete",
    "delete_all_for_owner",
    "get_accessible",
    "get_owned",
    "list_accessible",
    "list_for_owner",
    "list_for_owner_for_update",
    "names_by_ids",
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


async def list_for_owner_for_update(
    session: AsyncSession, app_user_id: uuid.UUID
) -> list[Pet]:
    """탈퇴가 지울 반려견을 잠가 새 gait FK 참조가 끼어들지 못하게 합니다."""
    stmt = (
        select(Pet)
        .where(Pet.app_user_id == app_user_id)
        .order_by(Pet.created_at, Pet.id)
        .with_for_update()
    )
    return list(await session.scalars(stmt))


async def get_owned(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Pet | None:
    """**내 것일 때만** 돌려줍니다.

    PK 로만 찾으면 남의 강아지 id 를 넣어 남의 정보를 읽거나 지울 수 있습니다.
    소유자 조건을 이 함수 안에 묶어 둬서, 부르는 쪽이 잊을 자리를 없앱니다.
    """
    stmt = select(Pet).where(Pet.id == pet_id, Pet.app_user_id == app_user_id)
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


def _is_member(app_user_id: uuid.UUID):
    """구성원 = 대표 ∪ 돌보미. **쓰기·파기에는 쓰지 마세요** — 그쪽은 `get_owned` 입니다."""
    return or_(
        Pet.app_user_id == app_user_id,
        Pet.id.in_(
            select(PetMember.pet_id).where(PetMember.app_user_id == app_user_id)
        ),
    )


async def get_accessible(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Pet | None:
    """**구성원일 때** 돌려줍니다. 읽기와 기록이 이것을 씁니다.

    `get_owned` 와 이름을 갈라 둔 것이 의도입니다 — 프로필 수정·배웅·삭제는 대표만이라
    거기서 이 함수를 부르면 돌보미가 강아지를 지웁니다. 잘못 부른 것이 이름으로 보여야
    합니다.
    """
    stmt = select(Pet).where(Pet.id == pet_id, _is_member(app_user_id))
    if for_update:
        stmt = stmt.with_for_update(of=Pet)
    return await session.scalar(stmt)


async def count_accessible(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """**미니룸에 서는 아이 수.** 상한 검사가 이것을 씁니다 (docs/co-care.md §2).

    `count_for_owner` 와 다릅니다 — 저건 파기·소유 판단용이고 이건 화면 용량입니다.
    """
    stmt = select(func.count()).select_from(Pet).where(_is_member(app_user_id))
    return int(await session.scalar(stmt) or 0)


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


async def accessible_ids(
    session: AsyncSession, app_user_id: uuid.UUID, pet_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """주어진 id 중 **내가 돌보는 아이**만 (대표 ∪ 돌보미). `owned_ids` 의 구성원판입니다.

    산책을 올릴 때 동행한 아이를 거르는 데 씁니다 (docs/co-care.md §2 — 계획 리뷰의
    판단). 산책 **소유**는 여전히 올린 사람 것이고, 여기서 여는 것은 "누구를 태그할 수
    있나" 뿐입니다 — 밥·약을 적을 수 있는 사람이면 같이 걸었다고 적을 수도 있어야
    합니다. 이것이 안 열리면 돌보미가 태그된 산책이 **아예 만들어지지 않아**,
    `walk.count_for_pet_between` 에서 소유자 조건을 뺀 것이 무의미해집니다.

    `owned_ids` 는 그대로 둡니다 — 성취·점령 요약(`services/activity.py`)은 여전히
    사람 것이라 그쪽이 씁니다 (스펙 결정 ①).

    빈 목록이면 쿼리도 안 날립니다 — `IN ()` 은 DB 마다 다르게 굽니다.
    """
    if not pet_ids:
        return set()
    stmt = select(Pet.id).where(_is_member(app_user_id), Pet.id.in_(pet_ids))
    return set(await session.scalars(stmt))


async def list_accessible(session: AsyncSession, app_user_id: uuid.UUID) -> list[Pet]:
    """**내가 돌보는 아이 전부** (대표 ∪ 돌보미), 등록 순서대로.

    `list_for_owner` 와 같은 정렬(`created_at, id`)입니다 — 앱의 카드 순서가 그것이고,
    두 목록의 순서가 다르면 초대를 수락한 순간 카드가 재배열됩니다.

    ⚠️ **승계 대상을 여기서 고르지 마세요.** 대표를 지웠을 때 물려받을 아이는
    `list_for_owner` 로 골라야 합니다 — 여기서 고르면 남의 강아지를 내 대표로 세웁니다.
    """
    stmt = select(Pet).where(_is_member(app_user_id)).order_by(Pet.created_at, Pet.id)
    return list(await session.scalars(stmt))


async def count_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """마릿수. 상한 검사에 씁니다."""
    stmt = select(Pet.id).where(Pet.app_user_id == app_user_id)
    return len(list(await session.scalars(stmt)))


async def count_by_owners(
    session: AsyncSession, app_user_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """여러 주인의 마릿수를 **한 번에.** 회원 목록 화면이 씁니다.

    [count_for_owner] 를 한 명씩 부르면 한 쪽(50명)에 쿼리가 50번 나갑니다.

    **한 마리도 없는 주인은 키가 아예 없습니다** — `GROUP BY` 가 행을 안 만듭니다.
    부르는 쪽에서 `.get(id, 0)` 으로 읽으세요. 여기서 0 을 채워 돌려주지 않는 것은,
    그러려면 이 함수가 "물어본 id 전부"를 알아야 해서 빈 목록과 없는 회원이 섞이기
    때문입니다.
    """
    if not app_user_ids:
        # `IN ()` 은 SQL 문법이 아닙니다. 빈 쪽(회원이 0명)에서 실제로 옵니다.
        return {}
    stmt = (
        select(Pet.app_user_id, func.count())
        .where(Pet.app_user_id.in_(app_user_ids))
        .group_by(Pet.app_user_id)
    )
    return {owner: count for owner, count in (await session.execute(stmt)).all()}


async def names_by_ids(
    session: AsyncSession, pet_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """id → 이름. 회원 목록이 **대표 강아지 이름**을 붙이는 데 씁니다.

    대표는 `app_users.primary_pet_id` 에 있으므로(pets 쪽에 `is_primary` 가 없는
    이유는 `models/app_user.py`), 목록은 그 id 들을 모아 여기서 한 번에 이름으로
    바꿉니다 — 회원마다 상세를 부르면 한 쪽에 쿼리가 50번 나갑니다.

    **없는 id 는 키가 없습니다.** 실제로는 FK 가 `ON DELETE SET NULL` 이라 없는
    강아지를 가리키는 `primary_pet_id` 자체가 없지만, 부르는 쪽은 `.get()` 으로
    읽어 그 가정에 기대지 않습니다.
    """
    if not pet_ids:
        # `IN ()` 은 SQL 문법이 아닙니다. 대표가 아무도 없는 쪽에서 실제로 옵니다.
        return {}
    stmt = select(Pet.id, Pet.name).where(Pet.id.in_(pet_ids))
    return {pet_id: name for pet_id, name in (await session.execute(stmt)).all()}


def add(session: AsyncSession, pet: Pet) -> Pet:
    session.add(pet)
    return pet


async def find_by_photo_key(
    session: AsyncSession, storage_key: str, *, pending: bool
) -> Pet | None:
    """저장소 키 하나로 행을 찾습니다. **bridge 전용입니다.**

    ⚠️ **소유자 조건이 없는 유일한 조회입니다.** bridge 는 인증 헤더를 안 받습니다 —
       Signed URL 을 흉내 내는 자리라 헤더를 요구하면 저장소를 GCS 로 바꿀 때 앱
       코드가 또 바뀝니다. 대신 **backend 가 실제로 발급한 키인지**를 여기서 봅니다.
       이 검사가 없으면 아무나 임의 경로로 서버 디스크를 채울 수 있습니다
       (보행 bridge 가 2026-09-02 에 그 상태로 한 번 배포됐습니다).

    키에 uuid 가 들어 있어 추측이 안 되는 것이 나머지 절반입니다
    (`build_pet_photo_key`).

    :param pending: 올릴 때는 **대기 키**로(확정된 사진을 덮어쓰지 못하게),
        내려받을 때는 **확정 키**로 찾습니다.
    """
    column = Pet.photo_pending_key if pending else Pet.photo_storage_key
    return await session.scalar(select(Pet).where(column == storage_key))


async def delete(session: AsyncSession, pet: Pet) -> None:
    await session.delete(pet)


async def delete_all_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """탈퇴한 회원의 강아지를 전부 지웁니다.

    산책은 먼저 지워야 합니다. 강아지를 먼저 지우면 ``walk_pets`` 연결만 CASCADE로
    사라지고, 사람 소유인 ``walks``와 그 좌표는 그대로 남기 때문입니다.
    """
    result = await session.execute(
        sql_delete(Pet).where(Pet.app_user_id == app_user_id)
    )
    return result.rowcount or 0
