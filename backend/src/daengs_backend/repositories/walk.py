"""walks 조회·저장. 쿼리만 있고 판단은 없습니다.

"이미 올라온 산책인가"를 어떻게 다룰지는 services 가 정합니다.
commit 도 하지 않습니다 — 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from daengs_backend.models import Walk

__all__ = ["add", "get_by_client_session", "get_owned", "list_for_owner"]


async def list_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> list[Walk]:
    """내 산책 전부, **최근 순.**

    좌표는 안 붙입니다 — 목록에 좌표까지 실으면 스무 건에 수만 점이 딸려 옵니다.
    `started_at` 이 같을 수 있어 `id` 로 한 번 더 정렬합니다.
    """
    stmt = (
        select(Walk)
        .where(Walk.app_user_id == app_user_id)
        .order_by(Walk.started_at.desc(), Walk.id)
    )
    return list(await session.scalars(stmt))


async def get_owned(
    session: AsyncSession, app_user_id: uuid.UUID, walk_id: uuid.UUID
) -> Walk | None:
    """**내 것일 때만** 좌표까지 붙여서 돌려줍니다.

    소유자 조건을 이 함수 안에 묶어 두면 부르는 쪽이 잊을 자리가 없습니다
    (`repositories/pet.py` 와 같은 이유).

    `selectinload` 로 좌표를 같이 읽습니다. 지연 로딩이면 비동기 세션에서 접근하는
    순간 터집니다.
    """
    stmt = (
        select(Walk)
        .where(Walk.id == walk_id, Walk.app_user_id == app_user_id)
        .options(selectinload(Walk.points))
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
        .options(selectinload(Walk.points))
    )
    return await session.scalar(stmt)


def add(session: AsyncSession, walk: Walk) -> Walk:
    session.add(walk)
    return walk
