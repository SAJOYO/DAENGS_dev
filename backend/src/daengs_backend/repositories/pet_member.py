"""pet_members / pet_invites 조회·저장. 쿼리만 있고 판단은 없습니다.

"초대해도 되나"·"상한을 넘었나"·"승계 순서" 는 services/pet_member.py 가 정합니다.
commit 도 하지 않습니다.
"""

import uuid
from datetime import datetime

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Pet, PetInvite, PetMember

__all__ = [
    "add",
    "add_invite",
    "count_members",
    "count_valid_invites",
    "delete_expired_invites",
    "delete_invite",
    "delete_invites_for_pet",
    "get_invite_by_hash",
    "is_member",
    "list_members",
    "remove",
]


async def list_members(session: AsyncSession, pet_id: uuid.UUID) -> list[uuid.UUID]:
    """그 아이의 **돌보미** id 들, 참여 순서대로. **대표는 안 들어 있습니다.**"""
    stmt = (
        select(PetMember.app_user_id)
        .where(PetMember.pet_id == pet_id)
        .order_by(PetMember.joined_at, PetMember.app_user_id)
    )
    return list(await session.scalars(stmt))


async def is_member(
    session: AsyncSession, pet_id: uuid.UUID, app_user_id: uuid.UUID
) -> bool:
    """구성원인가. **대표도 True 입니다** — 구성원은 대표 ∪ 돌보미입니다."""
    stmt = select(Pet.id).where(Pet.id == pet_id, Pet.app_user_id == app_user_id)
    if await session.scalar(stmt) is not None:
        return True
    member = select(PetMember.pet_id).where(
        PetMember.pet_id == pet_id, PetMember.app_user_id == app_user_id
    )
    return await session.scalar(member) is not None


async def count_members(session: AsyncSession, pet_id: uuid.UUID) -> int:
    """구성원 수. **대표를 포함해서 셉니다** — 상한 검사가 이것을 씁니다."""
    stmt = select(func.count()).select_from(PetMember).where(PetMember.pet_id == pet_id)
    return int(await session.scalar(stmt) or 0) + 1


def add(session: AsyncSession, pet_id: uuid.UUID, app_user_id: uuid.UUID) -> PetMember:
    row = PetMember(pet_id=pet_id, app_user_id=app_user_id)
    session.add(row)
    return row


async def remove(
    session: AsyncSession, pet_id: uuid.UUID, app_user_id: uuid.UUID
) -> int:
    """지운 행 수. 0 이면 애초에 돌보미가 아니었습니다."""
    result = await session.execute(
        sql_delete(PetMember).where(
            PetMember.pet_id == pet_id, PetMember.app_user_id == app_user_id
        )
    )
    return int(result.rowcount or 0)


async def get_invite_by_hash(session: AsyncSession, token_hash: str) -> PetInvite | None:
    return await session.scalar(select(PetInvite).where(PetInvite.token_hash == token_hash))


async def count_valid_invites(
    session: AsyncSession, pet_id: uuid.UUID, now: datetime
) -> int:
    stmt = (
        select(func.count())
        .select_from(PetInvite)
        .where(PetInvite.pet_id == pet_id, PetInvite.expires_at > now)
    )
    return int(await session.scalar(stmt) or 0)


def add_invite(
    session: AsyncSession,
    *,
    pet_id: uuid.UUID,
    invited_by: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
) -> PetInvite:
    row = PetInvite(
        pet_id=pet_id, invited_by=invited_by, token_hash=token_hash, expires_at=expires_at
    )
    session.add(row)
    return row


async def delete_invite(session: AsyncSession, invite_id: uuid.UUID) -> int:
    result = await session.execute(sql_delete(PetInvite).where(PetInvite.id == invite_id))
    return int(result.rowcount or 0)


async def delete_expired_invites(
    session: AsyncSession, pet_id: uuid.UUID, now: datetime
) -> int:
    """만료건 청소. **새 초대를 만들 때 부릅니다** — 아무도 안 누르면 영원히 쌓입니다."""
    result = await session.execute(
        sql_delete(PetInvite).where(PetInvite.pet_id == pet_id, PetInvite.expires_at <= now)
    )
    return int(result.rowcount or 0)


async def delete_invites_for_pet(session: AsyncSession, pet_id: uuid.UUID) -> int:
    """그 아이의 초대 전부. 승계가 부릅니다 — 옛 대표가 뿌린 링크를 죽입니다."""
    result = await session.execute(sql_delete(PetInvite).where(PetInvite.pet_id == pet_id))
    return int(result.rowcount or 0)
