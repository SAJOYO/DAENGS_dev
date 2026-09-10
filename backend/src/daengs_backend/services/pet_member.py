"""공동 돌봄의 규칙. 트랜잭션 경계도 여기입니다 (docs/co-care.md §3).

**락은 `pets` 행에 겁니다.** `pet_members` 를 세고 INSERT 하는 사이에 다른 수락이 끼면
상한을 넘기고, 탈퇴 가드와도 같은 자원을 두고 줄을 서야 합니다.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.token import generate_refresh_token, hash_refresh_token
from daengs_backend.models import Pet, PetInvite
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import pet_member as member_repo
from daengs_backend.schemas.pet_member import MemberOut
from daengs_backend.services.pet import MAX_PETS_PER_USER, PetNotFoundError

log = logging.getLogger(__name__)

#: 한 강아지의 구성원 수(대표 포함). 가족이 쓰는 기능이라 넉넉하되 무한은 아닙니다.
MAX_MEMBERS_PER_PET = 5

#: 동시에 살아 있을 수 있는 초대. 아빠와 동생을 같이 부르는 것은 되고, 무한 발급은 막습니다.
MAX_ACTIVE_INVITES = 3

#: 초대 유효 기간.
INVITE_TTL = timedelta(hours=24)


class InviteNotFoundError(Exception):
    """없는 토큰. **이미 쓴 초대도 이것입니다** — 수락이 행을 지우기 때문입니다."""


class InviteExpiredError(Exception):
    """만료됐거나, 그새 대표가 바뀌어 무효가 됐습니다."""


class AlreadyOwnerError(Exception):
    """대표가 자기 초대를 수락했습니다. 트리거 ② 가 DB 에서도 막지만 여기서 먼저 거절합니다."""


class MemberLimitError(Exception):
    """구성원 상한."""


class PetLimitError(Exception):
    """수락자의 미니룸 상한."""


class InviteLimitError(Exception):
    """유효 초대 상한."""


class CannotRemoveOwnerError(Exception):
    """대표는 이 경로로 못 나갑니다. 승계 엔드포인트로 가야 합니다."""


class NotAllowedError(Exception):
    """남을 내보낼 수 있는 것은 대표뿐입니다."""


class NotAMemberError(Exception):
    """승계 대상이 돌보미가 아닙니다. 승계와 초대를 한 번에 하지 않습니다."""


async def create_invite(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> tuple[PetInvite, str]:
    """**대표만.** 평문 토큰은 이 반환값에만 있고 DB 에는 해시만 남습니다.

    :returns: (초대 행, 평문 토큰)
    """
    pet = await pet_repo.get_owned(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError

    now = datetime.now(UTC)
    # 아무도 안 누른 만료건이 영원히 쌓이는 것을 여기서 막습니다.
    await member_repo.delete_expired_invites(session, pet_id, now)

    if await member_repo.count_valid_invites(session, pet_id, now) >= MAX_ACTIVE_INVITES:
        raise InviteLimitError

    token = generate_refresh_token()
    invite = member_repo.add_invite(
        session,
        pet_id=pet_id,
        invited_by=app_user_id,
        token_hash=hash_refresh_token(token),
        expires_at=now + INVITE_TTL,
    )
    await session.commit()
    return invite, token


async def accept_invite(session: AsyncSession, app_user_id: uuid.UUID, token: str) -> Pet:
    """초대 수락. 검증 순서는 docs/co-care.md §3 의 표와 같습니다."""
    invite = await member_repo.get_invite_by_hash(session, hash_refresh_token(token))
    if invite is None:
        raise InviteNotFoundError

    # ⚠️ pets 행을 잠급니다. 동시 수락이 상한을 넘기지 못하게 하고, 아래 3번 검사가
    #    동시 승계와 경쟁하지 않게 합니다.
    pet = await pet_repo.get_by_id_for_update(session, invite.pet_id)
    if pet is None:  # pragma: no cover — FK 가 CASCADE 라 강아지 없이 초대만 남을 수 없습니다
        raise InviteNotFoundError

    now = datetime.now(UTC)
    if invite.expires_at <= now:
        await member_repo.delete_invite(session, invite.id)
        await session.commit()
        raise InviteExpiredError

    if invite.invited_by != pet.app_user_id:
        # 그새 대표가 바뀌었습니다. 옛 대표가 뿌린 링크는 죽습니다.
        raise InviteExpiredError

    if pet.app_user_id == app_user_id:
        raise AlreadyOwnerError

    if await member_repo.is_member(session, pet.id, app_user_id):
        # 멱등입니다 — 동시 수락에서 진 쪽이 여기로 옵니다 (docs/co-care.md §3 의 행 5).
        #
        # **여기서도 초대를 지웁니다.** "수락하면 행을 지운다 — 그것만으로 일회용" 이
        # 이 표의 규칙이라, 이 분기만 초대를 살려 두면 토큰이 24시간까지 유효하게 남고
        # `MAX_ACTIVE_INVITES` 한 자리를 계속 먹으며, 나중에 이 사람을 내보내도 같은
        # 링크로 다시 들어옵니다. 이미 지워진 행을 지우는 것은 no-op(rowcount 0)이라
        # 이긴 쪽의 삭제와 경쟁하지 않습니다 — 락은 이미 `pets` 행이 잡고 있습니다.
        await member_repo.delete_invite(session, invite.id)
        await session.commit()
        return pet

    if await member_repo.count_members(session, pet.id) >= MAX_MEMBERS_PER_PET:
        raise MemberLimitError

    if await pet_repo.count_accessible(session, app_user_id) >= MAX_PETS_PER_USER:
        raise PetLimitError

    member_repo.add(session, pet.id, app_user_id)
    await member_repo.delete_invite(session, invite.id)

    # 등록한 강아지가 없던 사람은 첫 수락에서 대표 강아지를 얻습니다 —
    # 없으면 앱 첫 화면이 빕니다 (`services/pet.py` 의 등록 경로와 같은 규칙).
    user = await app_user_repo.get_by_id(session, app_user_id)
    if user is not None and user.primary_pet_id is None:
        user.primary_pet_id = pet.id

    await session.commit()
    log.info("공동 돌봄 참여 (pet=%s, user=%s)", pet.id, app_user_id)
    return pet


async def list_members(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> list[MemberOut]:
    """대표를 맨 앞에, 그다음 돌보미를 참여 순으로. **구성원만 볼 수 있습니다.**"""
    pet = await pet_repo.get_accessible(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError

    carer_ids = await member_repo.list_members(session, pet_id)
    names = await app_user_repo.nicknames_by_ids(session, [pet.app_user_id, *carer_ids])
    out = [
        MemberOut(
            app_user_id=pet.app_user_id,
            nickname=names.get(pet.app_user_id),
            is_owner=True,
        )
    ]
    out += [
        MemberOut(app_user_id=cid, nickname=names.get(cid), is_owner=False)
        for cid in carer_ids
    ]
    return out


async def remove_member(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    target_id: uuid.UUID,
) -> None:
    """내보내기(대표) 또는 나가기(본인). 같은 경로입니다."""
    pet = await pet_repo.get_accessible(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError
    if target_id == pet.app_user_id:
        raise CannotRemoveOwnerError
    if app_user_id != pet.app_user_id and app_user_id != target_id:
        raise NotAllowedError

    await member_repo.remove(session, pet_id, target_id)

    # ⚠️ `primary_pet_id` 의 FK 는 ON DELETE SET NULL 이지만 **강아지 행은 안 지워지므로
    #    안 돕니다.** 여기서 명시로 비웁니다 — 안 그러면 접근 못 하는 아이를 가리킵니다.
    user = await app_user_repo.get_by_id(session, target_id)
    if user is not None and user.primary_pet_id == pet_id:
        remaining = await pet_repo.list_accessible(session, target_id)
        user.primary_pet_id = remaining[0].id if remaining else None

    await session.commit()


async def transfer_owner(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    new_owner_id: uuid.UUID,
) -> Pet:
    """대표를 넘깁니다. **대표만.**

    ⚠️ **순서가 중요합니다.** 옛 대표를 `pet_members` 에 먼저 넣으면 `pet_members_not_owner`
    트리거가 터집니다 — 그 순간 옛 대표는 아직 `pets.app_user_id` 입니다. 그래서
    ① `pets.app_user_id` 를 새 대표로 먼저 바꾸고, ② 새 대표의 돌보미 행을 지운 뒤,
    ③ 그제서야 옛 대표를 돌보미로 넣습니다.
    """
    pet = await pet_repo.get_owned(session, app_user_id, pet_id, for_update=True)
    if pet is None:
        raise PetNotFoundError
    if new_owner_id == pet.app_user_id:
        # 대표가 자기 자신을 지목했습니다. `is_member` 는 대표도 True 로 치므로 이 검사가
        # 없으면 아래를 그대로 통과해 무의미한 UPDATE 뒤 `member_repo.add(self)` 에서
        # `pet_members_not_owner` 트리거가 터집니다(아직 안 잡히는 예외 → 500). 더 구체적인
        # 답이 이기도록 멤버십 검사보다 먼저 둡니다.
        raise AlreadyOwnerError
    if not await member_repo.is_member(session, pet_id, new_owner_id):
        raise NotAMemberError

    # 승계는 새 대표의 **소유**를 늘립니다. 미니룸 상한은 소유 기준이 아니지만
    # (`count_accessible`), 이 검사만은 소유로 봅니다 — 방에 선 아이 수는 안 변하고
    # (그 아이는 이미 새 대표의 방에 서 있습니다) 늘어나는 것은 소유뿐이기 때문입니다.
    if await pet_repo.count_for_owner(session, new_owner_id) >= MAX_PETS_PER_USER:
        raise PetLimitError

    pet.app_user_id = new_owner_id                              # ① 먼저
    await member_repo.remove(session, pet_id, new_owner_id)     # ②
    member_repo.add(session, pet_id, app_user_id)               # ③ 이제 안전
    await member_repo.delete_invites_for_pet(session, pet_id)   # ④ 옛 링크를 죽인다

    # 옛 대표의 primary_pet_id 는 건드리지 않습니다 — 돌보미로 남아 계속 접근합니다.
    await session.commit()
    log.info("대표 승계 (pet=%s, %s → %s)", pet_id, app_user_id, new_owner_id)
    return pet


async def actor_label(
    session: AsyncSession, pet_id: uuid.UUID, app_user_id: uuid.UUID | None
) -> str | None:
    """케어 기록에 이름을 낼지 정합니다 — **지금도 구성원일 때만** 냅니다.

    탈퇴자는 트리거가 `pet_members` 에서 지우므로 자동으로 비구성원이 되고, **재가입해도**
    다시 초대받기 전엔 이름이 안 납니다. 이 규칙이 없으면 옛 기록이 어느 날 갑자기 남의
    현재 닉네임으로 뜹니다 (docs/co-care.md §3).
    """
    if app_user_id is None:
        return None
    if not await member_repo.is_member(session, pet_id, app_user_id):
        return None
    names = await app_user_repo.nicknames_by_ids(session, [app_user_id])
    return names.get(app_user_id)


__all__ = [
    "INVITE_TTL",
    "MAX_ACTIVE_INVITES",
    "MAX_MEMBERS_PER_PET",
    "AlreadyOwnerError",
    "CannotRemoveOwnerError",
    "InviteExpiredError",
    "InviteLimitError",
    "InviteNotFoundError",
    "MemberLimitError",
    "NotAMemberError",
    "NotAllowedError",
    "PetLimitError",
    "accept_invite",
    "actor_label",
    "create_invite",
    "list_members",
    "remove_member",
    "transfer_owner",
]
