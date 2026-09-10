"""pet_members / pet_invites 조회·저장. 쿼리만 있고 판단은 없습니다.

"초대해도 되나"·"상한을 넘었나"·"승계 순서" 는 services/pet_member.py 가 정합니다.
commit 도 하지 않습니다.
"""

import uuid
from datetime import datetime

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Pet, PetInvite, PetMember

__all__ = [
    "add",
    "add_invite",
    "count_members",
    "count_valid_invites",
    "delete_expired_invites",
    "delete_invite",
    "delete_invite_for_pet",
    "delete_invites_for_pet",
    "get_invite_by_hash",
    "is_member",
    "list_invites",
    "list_members",
    "members_in",
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


async def members_in(
    session: AsyncSession, pairs: list[tuple[uuid.UUID, uuid.UUID]]
) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """주어진 (pet_id, app_user_id) 짝 중 **`pet_members` 에 돌보미로 있는 것**만.

    `is_member` 의 목록판입니다 — 대표 여부는 안 봅니다(그건 `pet_repo.owners_by_ids`
    가 따로 압니다). 응답 하나에 여러 (강아지, 사람) 짝의 구성원 여부를 물을 때
    (`services/pet_member.py::actor_labels`) 짝마다 `is_member` 를 부르면 목록 크기만큼
    왕복합니다 — 여기서는 한 번에 받습니다.

    빈 목록이면 쿼리도 안 날립니다.
    """
    if not pairs:
        return set()
    stmt = select(PetMember.pet_id, PetMember.app_user_id).where(
        tuple_(PetMember.pet_id, PetMember.app_user_id).in_(pairs)
    )
    return {(pet_id, uid) for pet_id, uid in (await session.execute(stmt)).all()}


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
    """**아직 쓸 수 있는** 초대 수. `MAX_ACTIVE_INVITES` 상한이 이것을 봅니다.

    수락된 행(영수증)은 뺍니다 — 예전에는 수락과 동시에 행이 지워져 상한 자리가 자동으로
    비었지만, 지금은 영수증이 `expires_at` 까지 남아 있습니다. 빼지 않으면 3명이 수락한
    강아지는 그 3개가 최대 24시간 동안 상한을 계속 먹어 새로 초대를 못 보냅니다.
    """
    stmt = (
        select(func.count())
        .select_from(PetInvite)
        .where(
            PetInvite.pet_id == pet_id,
            PetInvite.expires_at > now,
            PetInvite.accepted_by.is_(None),
        )
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


async def delete_invite_for_pet(
    session: AsyncSession, pet_id: uuid.UUID, invite_id: uuid.UUID
) -> int:
    """취소(`DELETE /app/pets/{pet_id}/invites/{invite_id}`) 전용.

    `pet_id` 를 같이 거는 이유는 URL 의 두 id 가 실제로 짝인지 DB 에 묻기 위해서입니다 —
    남의 강아지의 초대 id 를 넣어 보는 자리를 열지 않습니다. 0 행이면 서비스가 404 로
    답합니다(대표가 아니거나, 그 강아지의 초대가 아니거나, 애초에 없는 id — 셋 다 같은
    응답이라 정보가 안 샙니다).
    """
    result = await session.execute(
        sql_delete(PetInvite).where(PetInvite.id == invite_id, PetInvite.pet_id == pet_id)
    )
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
    """그 아이의 초대 전부. 승계가 부릅니다 — 옛 대표가 뿌린 링크를 죽입니다.

    수락된 영수증도 같이 지웁니다 — 손대지 않은 동작입니다. 승계 직후 "그새 대표가
    바뀌었다" 재시도 창을 남기는 것보다 옛 대표의 흔적을 한 번에 지우는 쪽이 이 트랜잭션의
    본래 목적(④ 옛 링크를 죽인다)에 더 맞습니다.
    """
    result = await session.execute(sql_delete(PetInvite).where(PetInvite.pet_id == pet_id))
    return int(result.rowcount or 0)


async def list_invites(session: AsyncSession, pet_id: uuid.UUID) -> list[PetInvite]:
    """그 아이의 초대 전부, 만든 순서대로. **수락된 것도 포함합니다** —
    `GET /app/pets/{pet_id}/invites` 가 이것을 씁니다. 평문 토큰이 이 응답에 한 번만
    나오므로(`InviteCreated`), 대표가 나중에 그 초대를 다시 찾을 유일한 길이 이 목록입니다.
    """
    stmt = (
        select(PetInvite)
        .where(PetInvite.pet_id == pet_id)
        .order_by(PetInvite.created_at, PetInvite.id)
    )
    return list(await session.scalars(stmt))
