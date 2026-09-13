"""pet_members / pet_invites 조회·저장. 쿼리만 있고 판단은 없습니다.

"초대해도 되나"·"상한을 넘었나"·"승계 순서" 는 services/pet_member.py 가 정합니다.
commit 도 하지 않습니다.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Pet, PetInvite, PetInvitePet, PetMember

__all__ = [
    "add",
    "add_invite",
    "add_invite_pets",
    "count_members",
    "count_valid_invites",
    "count_valid_invites_for_inviter",
    "delete_expired_invites",
    "delete_expired_invites_for_inviter",
    "delete_invite",
    "delete_invite_for_inviter",
    "delete_invite_for_pet",
    "delete_invite_with_pet",
    "delete_invites_for_pet",
    "get_invite_by_hash",
    "is_member",
    "list_bundle",
    "list_bundles",
    "list_invites",
    "list_invites_for_inviter",
    "list_invites_with_pet",
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
    pet_count: int = 1,
) -> PetInvite:
    """초대 본체. `pet_id` 는 **앵커**이고 묶음의 나머지는 `add_invite_pets` 가 넣습니다.

    `pet_count` 는 묶음의 원래 마릿수입니다 — 나중에 강아지가 지워져 자식 줄이 사라져도
    구성이 바뀌었다는 것을 이 값으로 알아냅니다 (MVP 결정 §2 "묶음 불변성").
    기본값 1 은 한 마리 초대가 이 저장소의 오래된 모양이라서입니다.
    """
    row = PetInvite(
        pet_id=pet_id,
        invited_by=invited_by,
        token_hash=token_hash,
        expires_at=expires_at,
        pet_count=pet_count,
    )
    session.add(row)
    return row


def add_invite_pets(
    session: AsyncSession, invite_id: uuid.UUID, pet_ids: Sequence[uuid.UUID]
) -> list[PetInvitePet]:
    """묶음에 담긴 강아지들. **앵커도 여기 한 줄로 들어갑니다** — 앵커만 부모 표에 있고
    자식 표에 없으면 `list_bundle` 이 그 아이를 빠뜨립니다."""
    rows = [PetInvitePet(invite_id=invite_id, pet_id=pet_id) for pet_id in pet_ids]
    session.add_all(rows)
    return rows


async def list_bundle(
    session: AsyncSession, invite_id: uuid.UUID
) -> list[PetInvitePet]:
    """그 초대에 담긴 강아지 줄 전부, pet id 순.

    **부르는 쪽은 빈 목록을 앵커 하나로 봐야 합니다** — 마이그레이션이 서버보다 먼저
    나가는 창(MVP 결정 §9)에서 옛 코드가 만든 초대는 자식 줄이 없습니다.
    """
    stmt = (
        select(PetInvitePet)
        .where(PetInvitePet.invite_id == invite_id)
        .order_by(PetInvitePet.pet_id)
    )
    return list(await session.scalars(stmt))


async def list_bundles(
    session: AsyncSession, invite_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """여러 초대의 강아지 목록을 **한 번에**. 초대 목록 응답이 씁니다 — 초대마다
    `list_bundle` 을 부르면 목록 크기만큼 왕복합니다.

    빈 목록이면 쿼리도 안 날립니다.
    """
    if not invite_ids:
        return {}
    stmt = (
        select(PetInvitePet.invite_id, PetInvitePet.pet_id)
        .where(PetInvitePet.invite_id.in_(set(invite_ids)))
        .order_by(PetInvitePet.pet_id)
    )
    bundles: dict[uuid.UUID, list[uuid.UUID]] = {}
    for invite_id, pet_id in (await session.execute(stmt)).all():
        bundles.setdefault(invite_id, []).append(pet_id)
    return bundles


async def count_valid_invites_for_inviter(
    session: AsyncSession, invited_by: uuid.UUID, now: datetime
) -> int:
    """**아직 쓸 수 있는** 묶음 수 — 사람 한 명이 기준입니다 (MVP 결정 §2).

    `count_valid_invites` 는 강아지당이었습니다. 묶음 하나에 다섯 마리가 들어가면
    강아지당으로 세는 것은 "묶음 하나 = 활성 하나" 와 어긋납니다 — 같은 묶음이 다섯
    자리를 먹거나, 반대로 강아지마다 3묶음씩 만들어 15묶음이 살아 있게 됩니다.

    수락된 행(영수증)은 뺍니다 — `count_valid_invites` 와 같은 규칙입니다.
    """
    stmt = (
        select(func.count())
        .select_from(PetInvite)
        .where(
            PetInvite.invited_by == invited_by,
            PetInvite.expires_at > now,
            PetInvite.accepted_by.is_(None),
        )
    )
    return int(await session.scalar(stmt) or 0)


async def list_invites_for_inviter(
    session: AsyncSession, invited_by: uuid.UUID
) -> list[PetInvite]:
    """내가 보낸 묶음 전부, 만든 순서대로. `GET /app/pet-invites` 가 씁니다."""
    stmt = (
        select(PetInvite)
        .where(PetInvite.invited_by == invited_by)
        .order_by(PetInvite.created_at, PetInvite.id)
    )
    return list(await session.scalars(stmt))


async def list_invites_with_pet(
    session: AsyncSession, pet_id: uuid.UUID
) -> list[PetInvite]:
    """**그 아이가 낀 묶음** 전부. 구 경로 `GET /app/pets/{pet_id}/invites` 가 씁니다.

    자식 표를 조인하므로 앵커가 아닌 아이로도 찾힙니다. 자식 줄이 아직 없는 옛 초대는
    앵커(`pet_invites.pet_id`)로도 걸리게 `OR` 로 묶습니다 — 배포 창의 그 초대도
    대표에게 보여야 취소할 수 있습니다.
    """
    stmt = (
        select(PetInvite)
        .where(
            or_(
                PetInvite.pet_id == pet_id,
                select(PetInvitePet.invite_id)
                .where(
                    PetInvitePet.invite_id == PetInvite.id,
                    PetInvitePet.pet_id == pet_id,
                )
                .exists(),
            )
        )
        .order_by(PetInvite.created_at, PetInvite.id)
    )
    return list(await session.scalars(stmt))


async def delete_invite_for_inviter(
    session: AsyncSession, invited_by: uuid.UUID, invite_id: uuid.UUID
) -> int:
    """묶음 **전체** 취소. `invited_by` 를 같이 거는 이유는 `delete_invite_for_pet` 이
    `pet_id` 를 같이 거는 것과 같습니다 — 남의 초대 id 를 넣어 보는 자리를 안 만듭니다.

    자식 줄은 FK CASCADE 가 같이 지웁니다.
    """
    result = await session.execute(
        sql_delete(PetInvite).where(
            PetInvite.id == invite_id, PetInvite.invited_by == invited_by
        )
    )
    return int(result.rowcount or 0)


async def delete_invite_with_pet(
    session: AsyncSession,
    invited_by: uuid.UUID,
    pet_id: uuid.UUID,
    invite_id: uuid.UUID,
) -> int:
    """묶음 **전체** 취소 — 단, 그 아이가 **실제로 담긴** 묶음일 때만.

    구 경로 `DELETE /app/pets/{pet_id}/invites/{invite_id}` 전용입니다. URL 의 두 id 가
    짝인지 DB 에 묻는 것이 `delete_invite_for_pet` 의 원래 목적이었고, 묶음이 되면서 그
    "짝" 의 뜻이 **앵커이거나 담긴 아이이거나**로 넓어졌습니다. 이 검사를 빼면 같은 사람이
    가진 **다른 아이의 초대**를 아무 경로로나 지울 수 있게 됩니다.

    0 행이면 서비스가 404 로 답합니다 — 남의 초대·없는 조합·이미 없는 id 가 같은 응답이라
    정보가 안 샙니다.
    """
    result = await session.execute(
        sql_delete(PetInvite).where(
            PetInvite.id == invite_id,
            PetInvite.invited_by == invited_by,
            or_(
                PetInvite.pet_id == pet_id,
                select(PetInvitePet.invite_id)
                .where(
                    PetInvitePet.invite_id == PetInvite.id,
                    PetInvitePet.pet_id == pet_id,
                )
                .exists(),
            ),
        )
    )
    return int(result.rowcount or 0)


async def delete_expired_invites_for_inviter(
    session: AsyncSession, invited_by: uuid.UUID, now: datetime
) -> int:
    """그 사람이 뿌린 만료건 청소. 새 묶음을 만들 때 부릅니다 —
    `delete_expired_invites` 의 사람 기준판입니다."""
    result = await session.execute(
        sql_delete(PetInvite).where(
            PetInvite.invited_by == invited_by, PetInvite.expires_at <= now
        )
    )
    return int(result.rowcount or 0)


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
