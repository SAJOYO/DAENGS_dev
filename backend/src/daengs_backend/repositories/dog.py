"""dogs 조회·저장. 쿼리만 있고 판단은 없습니다.

소유권 판정을 여기서 하지 않습니다 — `get_by_id` 는 누구의 개든 있는 그대로
돌려주고, "이 회원이 봐도 되나"는 services 가 정합니다 (app_user 레포와 같은 규칙).

commit 은 하지 않습니다. 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Dog

__all__ = ["create", "get_by_id", "list_by_owner"]


async def create(
    session: AsyncSession,
    *,
    app_user_id: uuid.UUID,
    name: str,
    breed: str | None,
    birth_date: date | None,
    sex: str | None,
    neutered: bool | None,
    weight_kg: float | None,
    size_class: str,
) -> Dog:
    """강아지 한 줄을 만듭니다. id 와 created_at 은 DB 가 채웁니다.

    flush 만 합니다 (commit 아님 — 롤백은 그대로 됩니다). 방금 등록한 강아지를
    응답으로 바로 돌려줘야 해서입니다.
    """
    dog = Dog(
        app_user_id=app_user_id,
        name=name,
        breed=breed,
        birth_date=birth_date,
        sex=sex,
        neutered=neutered,
        weight_kg=weight_kg,
        size_class=size_class,
    )
    session.add(dog)
    await session.flush()
    return dog


async def get_by_id(session: AsyncSession, dog_id: uuid.UUID) -> Dog | None:
    """PK 로 한 마리. 소유권으로 거르지 않습니다 — 그건 services 의 판단입니다."""
    return await session.get(Dog, dog_id)


async def list_by_owner(session: AsyncSession, app_user_id: uuid.UUID) -> list[Dog]:
    """이 회원의 강아지 전부. 등록 순서대로 — 화면의 "첫째, 둘째"가 흔들리지 않게."""
    stmt = (
        select(Dog)
        .where(Dog.app_user_id == app_user_id)
        .order_by(Dog.created_at, Dog.id)
    )
    return list((await session.scalars(stmt)).all())
