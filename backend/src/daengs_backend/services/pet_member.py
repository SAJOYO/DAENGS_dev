"""공동 돌봄의 규칙. 트랜잭션 경계도 여기입니다 (docs/co-care.md §3).

**락은 `pets` 행에 겁니다.** `pet_members` 를 세고 INSERT 하는 사이에 다른 수락이 끼면
상한을 넘기고, 탈퇴 가드와도 같은 자원을 두고 줄을 서야 합니다.
"""

import logging
import uuid
from collections.abc import Iterable
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
    """없는 토큰. **다른 사람이 이미 쓴 토큰도 이것입니다** — 그 토큰이 한 번이라도 존재했다는
    사실 자체를 안 알려주기 위해서입니다(§3 "행 3/4"). 같은 사람이 같은 토큰을 다시 보내는
    것은 이것이 아니라 영수증 200 입니다 — `accept_invite` 를 보세요.
    """


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
    """초대 수락. 검증 순서는 docs/co-care.md § 3 의 표와 같습니다.

    **수락은 더 이상 초대 행을 지우지 않습니다** (2026-09-10, #388 · #261). 대신
    `accepted_at`/`accepted_by` 를 채워 영수증으로 남깁니다 — 응답을 못 받은 재시도가
    행이 사라져 404 를 받으면, 이미 다른 강아지를 돌보는 사람은 그것이 "이번 요청이
    실패했다" 인지 "이미 성공했는데 응답만 못 받았다" 인지 구별할 방법이 없었습니다.
    영수증이 있으면 **같은 토큰 + 같은 사람**은 그대로 같은 `{pet_id, name}` 을 다시
    받고, **다른 사람**은 여전히 404 입니다(그 토큰이 존재했다는 사실 자체를 안 새게).

    ⚠️ 영수증 분기는 그 사이 다른 일(탈퇴·내보내기)이 있었는지 다시 확인하지 않습니다 —
    "응답을 못 받은 재시도" 는 거의 곧바로 다시 오는 것을 전제하기 때문입니다. 그 사이
    실제로 나간 사람을 되살리고 싶으면 새 초대를 받아야 합니다.
    """
    invite = await member_repo.get_invite_by_hash(session, hash_refresh_token(token))
    if invite is None:
        raise InviteNotFoundError

    # ⚠️ pets 행을 잠급니다. 동시 수락이 상한을 넘기지 못하게 하고, 아래 검사가
    #    동시 승계와 경쟁하지 않게 합니다.
    pet = await pet_repo.get_by_id_for_update(session, invite.pet_id)
    if pet is None:  # pragma: no cover — FK 가 CASCADE 라 강아지 없이 초대만 남을 수 없습니다
        raise InviteNotFoundError

    now = datetime.now(UTC)
    if invite.expires_at <= now:
        # 영수증이든 아니든 수명은 expires_at 까지입니다 — 지나면 지웁니다.
        await member_repo.delete_invite(session, invite.id)
        await session.commit()
        raise InviteExpiredError

    if invite.accepted_by is not None:
        # 이미 쓴 토큰입니다. **같은 사람이면 그때의 응답을 그대로 돌려줍니다** — 새로
        # 아무것도 확인하지 않습니다(위 docstring 참고). 다른 사람이면 "없는 토큰" 과
        # 똑같은 404 입니다 — 그래야 그 토큰이 한 번이라도 유효했다는 사실이 안 샙니다.
        if invite.accepted_by == app_user_id:
            return pet
        raise InviteNotFoundError

    if invite.invited_by != pet.app_user_id:
        # 그새 대표가 바뀌었습니다. 옛 대표가 뿌린 링크는 죽습니다.
        raise InviteExpiredError

    if pet.app_user_id == app_user_id:
        raise AlreadyOwnerError

    if await member_repo.is_member(session, pet.id, app_user_id):
        # 멱등입니다 — 동시 수락에서 진 쪽이 여기로 옵니다(docs/co-care.md § 3 의 행 7).
        # 이 함수 안에서 딱 한 번 읽은 `invite` 객체라 위 `accepted_by` 분기는 못 봤을
        # 수 있습니다(이긴 쪽이 그새 커밋했더라도 이 객체는 그 값을 다시 읽지 않습니다) —
        # 그래서 여기서도 영수증을 채웁니다. 이래야 이 사람이 **다음** 재시도부터는
        # 바로 위 분기(빠른 경로)로 들어옵니다.
        invite.accepted_at = now
        invite.accepted_by = app_user_id
        await session.commit()
        return pet

    if await member_repo.count_members(session, pet.id) >= MAX_MEMBERS_PER_PET:
        raise MemberLimitError

    if await pet_repo.count_accessible(session, app_user_id) >= MAX_PETS_PER_USER:
        raise PetLimitError

    member_repo.add(session, pet.id, app_user_id)
    invite.accepted_at = now
    invite.accepted_by = app_user_id

    # 등록한 강아지가 없던 사람은 첫 수락에서 대표 강아지를 얻습니다 —
    # 없으면 앱 첫 화면이 빕니다 (`services/pet.py` 의 등록 경로와 같은 규칙).
    user = await app_user_repo.get_by_id(session, app_user_id)
    if user is not None and user.primary_pet_id is None:
        user.primary_pet_id = pet.id

    await session.commit()
    log.info("공동 돌봄 참여 (pet=%s, user=%s)", pet.id, app_user_id)
    return pet


async def list_invites(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> list[PetInvite]:
    """그 아이의 초대 전부, 만든 순서대로. **대표만** — 평문 토큰이 발급 응답에 한 번만
    나오므로, 나중에 그 초대를 찾아 취소하려면 이 목록이 유일한 길입니다. 해시도 토큰도
    안 돌려줍니다(라우터의 `InviteOut` 이 그 둘을 아예 담지 않습니다).
    """
    pet = await pet_repo.get_owned(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError
    return await member_repo.list_invites(session, pet_id)


async def cancel_invite(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID, invite_id: uuid.UUID
) -> None:
    """초대 취소. **대표만.** 돌보미·제3자는 강아지가 안 보이므로 404, 남의 강아지의
    초대 id 를 넣어도 404 — `delete_invite_for_pet` 이 `pet_id` 까지 같이 걸기 때문입니다.
    """
    pet = await pet_repo.get_owned(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError
    deleted = await member_repo.delete_invite_for_pet(session, pet_id, invite_id)
    if deleted == 0:
        raise InviteNotFoundError
    await session.commit()


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


async def actor_labels(
    session: AsyncSession,
    pairs: Iterable[tuple[uuid.UUID | None, uuid.UUID | None]],
    *,
    owners: dict[uuid.UUID, uuid.UUID] | None = None,
) -> dict[tuple[uuid.UUID, uuid.UUID], str | None]:
    """`actor_label` 의 목록판 — 응답 하나(gait·screening 목록)에 **쿼리 세 번**으로 끝냅니다.

    행마다 `actor_label` 을 부르면 N 개 목록에 `is_member` 왕복이 N 번입니다 (Task 19,
    "watch the query cost" 요구사항). 여기서는 (pet_id, app_user_id) 짝을 모아
      ① 대표 맵(`pet_repo.owners_by_ids`, 이미 있으면 재사용 — `owners` 인자)
      ② 돌보미 여부(`member_repo.members_in`)
      ③ 닉네임(`app_user_repo.nicknames_by_ids`)
    셋만 묻습니다. 목록 크기(N)가 30 이든 300 이든 쿼리 수는 그대로입니다.

    `pet_id`·`app_user_id` 가 None 인 짝(예: `pet_id IS NULL` 인 개인 스크리닝 기록,
    탈퇴로 `actor_app_user_id` 가 비워진 gait 기록)은 걸러 아예 묻지 않습니다 —
    "구성원" 이라는 개념 자체가 없거나 누구인지 모르니 결과는 항상 None 입니다.
    """
    valid = [(pet_id, uid) for pet_id, uid in pairs if pet_id is not None and uid is not None]
    if not valid:
        return {}

    if owners is None:
        owners = await pet_repo.owners_by_ids(session, list({pet_id for pet_id, _ in valid}))

    member_pairs = await member_repo.members_in(session, valid)

    current_members = {
        pair for pair in valid if owners.get(pair[0]) == pair[1] or pair in member_pairs
    }
    if not current_members:
        return dict.fromkeys(valid)

    names = await app_user_repo.nicknames_by_ids(
        session, list({uid for _, uid in current_members})
    )
    return {
        pair: (names.get(pair[1]) if pair in current_members else None) for pair in valid
    }


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
    "actor_labels",
    "cancel_invite",
    "create_invite",
    "list_invites",
    "list_members",
    "remove_member",
    "transfer_owner",
]
