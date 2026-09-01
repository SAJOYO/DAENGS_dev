"""walks 조회·저장. 쿼리만 있고 판단은 없습니다.

"이미 올라온 산책인가"를 어떻게 다룰지는 services 가 정합니다.
commit 도 하지 않습니다 — 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from daengs_backend.models import Walk, WalkPet, WalkPoint

__all__ = [
    "add",
    "delete_walks_only_with",
    "existing_seqs",
    "get_by_client_session",
    "get_owned",
    "list_for_owner",
]


async def list_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> list[Walk]:
    """내 산책 전부, **최근 순.**

    좌표는 안 붙입니다 — 목록에 좌표까지 실으면 스무 건에 수만 점이 딸려 옵니다.
    나간 아이들(`pets`)은 붙입니다. 산책당 많아야 몇 줄이고, 안 붙이면 응답을 만들다
    지연 로딩에서 터집니다.

    `started_at` 이 같을 수 있어 `id` 로 한 번 더 정렬합니다.
    """
    stmt = (
        select(Walk)
        .where(Walk.app_user_id == app_user_id)
        .options(selectinload(Walk.pets))
        .order_by(Walk.started_at.desc(), Walk.id)
    )
    return list(await session.scalars(stmt))


async def get_owned(
    session: AsyncSession, app_user_id: uuid.UUID, walk_id: uuid.UUID
) -> Walk | None:
    """**내 것일 때만** 좌표까지 붙여서 돌려줍니다.

    소유자 조건을 이 함수 안에 묶어 두면 부르는 쪽이 잊을 자리가 없습니다
    (`repositories/pet.py` 와 같은 이유).

    `selectinload` 로 좌표와 나간 아이들을 같이 읽습니다. 지연 로딩이면 비동기
    세션에서 접근하는 순간 터집니다.
    """
    stmt = (
        select(Walk)
        .where(Walk.id == walk_id, Walk.app_user_id == app_user_id)
        .options(selectinload(Walk.points), selectinload(Walk.pets))
    )
    return await session.scalar(stmt)


async def get_by_client_session(
    session: AsyncSession, app_user_id: uuid.UUID, client_session_id: uuid.UUID
) -> Walk | None:
    """기기가 준 id 로 찾습니다. **재시도를 안전하게 만드는 조회**입니다.

    앱은 네트워크가 끊기면 다음에 다시 올립니다. 그때 이미 올라온 것이면 새로 넣지
    않고 있던 것을 돌려줘야 같은 산책이 두 건이 되지 않습니다.
    """
    stmt = (
        select(Walk)
        .where(
            Walk.app_user_id == app_user_id,
            Walk.client_session_id == client_session_id,
        )
        .options(selectinload(Walk.points), selectinload(Walk.pets))
    )
    return await session.scalar(stmt)


async def delete_walks_only_with(session: AsyncSession, pet_id: uuid.UUID) -> int:
    """**그 아이와만** 나간 산책을 지웁니다.

    강아지를 지울 때 부릅니다. 다른 아이와 같이 나간 산책은 **남깁니다** — 그 산책은
    남은 아이의 기록이기도 해서, 지우면 그 아이의 운동량이 통째로 빕니다. 그 산책에서
    이 아이만 빠지는 것은 조인 행의 `ON DELETE CASCADE` 가 알아서 합니다.

    아무도 안 붙은 산책은 애초에 조인 행이 없어 여기 걸리지 않습니다.

    :returns: 지운 산책 수.
    """
    others = WalkPet.__table__.alias("others")
    solo = (
        select(WalkPet.walk_id)
        .where(
            WalkPet.pet_id == pet_id,
            ~exists().where(
                others.c.walk_id == WalkPet.walk_id,
                others.c.pet_id != pet_id,
            ),
        )
    )
    result = await session.execute(delete(Walk).where(Walk.id.in_(solo)))
    return result.rowcount or 0


def add(session: AsyncSession, walk: Walk) -> Walk:
    session.add(walk)
    return walk


async def existing_seqs(session: AsyncSession, walk_id: uuid.UUID) -> set[int]:
    """이미 저장된 좌표 순번.

    나눠 올릴 때 **같은 묶음이 두 번 와도** 조용히 넘기려고 씁니다. DB 의 PK 가
    막아 주기는 하지만, 그건 예외로 터지는 방식이라 재시도가 500 이 됩니다.
    """
    stmt = select(WalkPoint.client_seq).where(WalkPoint.walk_id == walk_id)
    return set(await session.scalars(stmt))
