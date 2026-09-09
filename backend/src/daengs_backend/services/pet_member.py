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
        # 멱등입니다 — 카톡 링크는 두 번 눌립니다. 초대는 그대로 두어야 첫 수락의
        # 삭제와 경쟁하지 않습니다.
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


__all__ = [
    "INVITE_TTL",
    "MAX_ACTIVE_INVITES",
    "MAX_MEMBERS_PER_PET",
    "AlreadyOwnerError",
    "InviteExpiredError",
    "InviteLimitError",
    "InviteNotFoundError",
    "MemberLimitError",
    "PetLimitError",
    "accept_invite",
    "create_invite",
]
