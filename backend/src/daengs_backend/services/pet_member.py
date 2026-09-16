"""공동 돌봄의 규칙. 트랜잭션 경계도 여기입니다 (docs/co-care.md §3).

**락은 `pets` 행에 겁니다.** `pet_members` 를 세고 INSERT 하는 사이에 다른 수락이 끼면
상한을 넘기고, 탈퇴 가드와도 같은 자원을 두고 줄을 서야 합니다.
"""

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.token import generate_refresh_token, hash_refresh_token
from daengs_backend.models import Pet, PetInvite
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import pet_identity as identity_repo
from daengs_backend.repositories import pet_member as member_repo
from daengs_backend.schemas.pet_member import MemberOut
from daengs_backend.services import pet_identity as identity_service
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
    """유효 초대 상한. **묶음 단위이고 사람 기준입니다** (MVP 결정 §2) — 강아지 수와
    무관하게 묶음 하나가 자리 하나입니다."""


class InviteBundleChangedError(Exception):
    """묶음 구성이 바뀌었습니다 — 강아지가 지워졌거나, 대표가 바뀌었거나, 배웅됐습니다.

    **남은 강아지만 부분 수락하지 않습니다** (MVP 결정 §2 "묶음 불변성"). 받는 사람이
    미리보기에서 본 것과 다른 것을 받게 되기 때문입니다. 라우터가 410 으로 바꿉니다.
    """


class PetFarewelledError(Exception):
    """배웅한 아이는 초대에도 연결 후보에도 못 넣습니다 (MVP 결정 §4 최소 안전안)."""

    def __init__(self, pet_name: str) -> None:
        self.pet_name = pet_name
        super().__init__(pet_name)


class CannotRemoveOwnerError(Exception):
    """**그룹 주보호자가 자기 자신**을 지목했습니다. 승계 엔드포인트로 가야 합니다.

    남이 그룹 주보호자를 지목한 경우는 이것이 아니라 `NotAllowedError` 입니다 — 그 사람은
    애초에 남을 내보낼 수 없고, 여기서 409 를 주면 "지목한 그 사람이 이 그룹의 주보호자다"
    가 새 나갑니다.
    """


class NotAllowedError(Exception):
    """**남을 내보낼 수 있는 것은 그룹 주보호자뿐입니다.** 라우터가 403 으로 바꿉니다.

    ⚠️ **요청한 행의 대표인지로 갈리지 않습니다.** 예전에는 그 행의 대표이기만 한 사람
    (= 연결한 공동 보호자가 자기 카드 id 로 부른 경우)에게만 409 `not_group_owner` 를
    주고 생 돌보미에게는 403 을 줬는데, 사용자 눈에는 둘 다 "공동 보호자가 남을 내보내려
    한 것" 하나입니다. 행 소유라는 **내부 사정**이 상태 코드를 가르면 앱이 같은 상황을 두
    갈래로 그려야 하고, 응답만 보고 "나는 이 행의 대표다" 를 알아낼 수 있습니다.
    """


class NotAMemberError(Exception):
    """승계 대상이 돌보미가 아닙니다. 승계와 초대를 한 번에 하지 않습니다."""


class LinkedOwnerTransferError(Exception):
    """**연결된 그룹에서, 대상이 이미 자기 행을 가진 경우의 승계입니다.**

    지금 승계는 앵커 행의 `pets.app_user_id` 를 대상으로 옮깁니다. 그런데 대상이 같은
    그룹에 이미 자기 행을 갖고 있으면 그 순간 한 사람이 한 그룹에 행 둘을 갖게 되어
    `pets_identity_one_per_user` 부분 UNIQUE 를 위반합니다 — **막지 않으면 500 입니다**
    (일회용 PostgreSQL 로 재현했습니다).

    제대로 지원하려면 행 소유를 옮기는 대신 `pet_identities.owner_pet_id` 를 대상의 행으로
    **옮겨야** 합니다. 그건 승계의 의미를 둘로 가르는 제품 결정이라 MVP 에서 하지 않고,
    여기서 **명시적으로 막습니다**. 라우터가 409 로 바꿉니다.

    우회로: 대상을 내보냈다가(연결이 같이 풀립니다) 다시 초대하면 연결 없이 참여합니다.
    """

    def __init__(self, pet_name: str) -> None:
        self.pet_name = pet_name
        super().__init__(pet_name)


class LinkSelectionRequiredError(Exception):
    """**묶음인데 강아지별 연결 선택이 안 왔습니다** (MVP 결정 §2).

    토큰만 보내는 옛 계약은 "전부 연결 없이 참여" 라는 뜻입니다. 한 마리 초대에서는 그것이
    유일한 선택지라 맞지만, **여러 마리 묶음에서는 사용자가 고를 것이 있는데 구 앱이 그
    화면을 못 그립니다.** 그대로 통과시키면 사용자가 모르는 사이에 전부 새 강아지로
    들어와 목록이 늘고, 되돌리려면 하나씩 나가야 합니다.

    그래서 **묶음이 두 마리 이상이면 모든 항목의 선택값을 요구**합니다. 라우터가 409 로
    바꾸고 `detail.code = "link_selection_required"` 와 빠진 `pet_id` 들을 같이 냅니다.
    """

    def __init__(self, missing: list[uuid.UUID]) -> None:
        self.missing = missing
        super().__init__(", ".join(str(i) for i in missing))


async def create_invite(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> tuple[PetInvite, str]:
    """한 마리 초대 — 구 경로 `POST /app/pets/{pet_id}/invites` 전용입니다.

    묶음 하나짜리로 만듭니다. 새 경로와 **같은 규칙·같은 상한**을 지나므로, 구 앱이
    만든 초대도 새 앱이 만든 것과 구별 없이 다뤄집니다.

    :returns: (초대 행, 평문 토큰)
    """
    invite, token, _pet_ids = await create_invite_bundle(session, app_user_id, [pet_id])
    return invite, token


async def create_invite_bundle(
    session: AsyncSession, app_user_id: uuid.UUID, pet_ids: list[uuid.UUID]
) -> tuple[PetInvite, str, list[uuid.UUID]]:
    """**강아지 여러 마리를 토큰 하나에** 담습니다 (MVP 결정 §2).

    평문 토큰은 이 반환값에만 있고 DB 에는 해시만 남습니다.

    검증 순서와 이유:

    1. **사용자 행을 먼저 잠급니다** — 활성 묶음 수를 세는 단위가 사람이라, 동시 요청을
       pet 잠금으로는 직렬화할 수 없습니다.
    2. 강아지를 **id 오름차순으로** 잠급니다 — 묶음 하나가 여러 행을 잠그므로, 순서를
       고정하지 않으면 두 요청이 서로를 기다리는 데드락이 생깁니다.
    3. 각 아이마다 **행 대표 + 그룹 주보호자**를 봅니다. 연결된 아이를 공동 보호자가
       초대하면 그 그룹에 사람을 마음대로 들이게 됩니다.
    4. **배웅한 아이는 못 넣습니다** — 배웅 상태를 그룹 공통으로 옮기는 것은 후속이라,
       그때까지는 애초에 초대에 안 들어가는 것이 안전합니다.
    5. 활성 묶음 상한은 **사람당** 입니다. 강아지당으로 세면 "묶음 하나 = 활성 하나" 가
       깨집니다 (`count_valid_invites_for_inviter` 독스트링).

    **앵커는 `pet_ids[0]`** 입니다. `pet_invites.pet_id` 가 NOT NULL 로 남아 있고 승계·
    만료 청소가 그 칸을 보므로, 묶음이어도 대표 한 마리를 골라 둡니다.

    :returns: (초대 행, 평문 토큰, 담긴 pet id 들)
    """
    # 1. 사람 단위 직렬화. `create_pet` 과 같은 이유입니다.
    await identity_service.lock_user(session, app_user_id)

    # 2~4. 잠그면서 검증합니다.
    for pet_id in sorted(pet_ids):
        pet = await pet_repo.get_owned(session, app_user_id, pet_id, for_update=True)
        if pet is None:
            raise PetNotFoundError
        await identity_service.require_group_owner(session, app_user_id, pet)
        if pet.farewell_on is not None:
            raise PetFarewelledError(pet.name)

    now = datetime.now(UTC)
    # 아무도 안 누른 만료건이 영원히 쌓이는 것을 여기서 막습니다.
    await member_repo.delete_expired_invites_for_inviter(session, app_user_id, now)

    # 5. 사람당 상한.
    if (
        await member_repo.count_valid_invites_for_inviter(session, app_user_id, now)
        >= MAX_ACTIVE_INVITES
    ):
        raise InviteLimitError

    token = generate_refresh_token()
    invite = member_repo.add_invite(
        session,
        pet_id=pet_ids[0],
        invited_by=app_user_id,
        token_hash=hash_refresh_token(token),
        expires_at=now + INVITE_TTL,
        pet_count=len(pet_ids),
    )
    # 자식 줄에 `invite.id` 가 필요합니다. DB 기본값을 미리 받아 옵니다.
    await session.flush()
    member_repo.add_invite_pets(session, invite.id, pet_ids)

    await session.commit()
    return invite, token, list(pet_ids)


async def bundle_pet_ids(
    session: AsyncSession, invite: PetInvite
) -> tuple[list[uuid.UUID], bool]:
    """묶음에 담긴 pet id 들과 **구성이 바뀌었는지**.

    `pet_invite_pets.pet_id` 가 CASCADE 라 강아지가 지워지면 자식 줄이 조용히 사라집니다.
    그래서 남은 줄 수를 `pet_invites.pet_count` 와 견줘 구성 변경을 알아냅니다 — 안 보면
    남은 강아지만 **부분 수락**되는데, 그것이 제품이 금지한 동작입니다.

    ⚠️ **자식 줄이 하나도 없으면 바뀐 것이 아니라 옛 초대입니다.** 마이그레이션이 서버보다
    먼저 나가는 창(MVP 결정 §9)에서 옛 코드가 만든 초대에는 자식 줄이 없습니다 — 그때는
    앵커 하나짜리 묶음으로 봅니다.

    ⚠️ **앵커 자체가 지워지면 초대 행이 통째로 사라집니다** (`pet_invites.pet_id` 의
    CASCADE). 그 토큰은 여기 오지 못하고 404 입니다 — 410 이 아닙니다. 구성 변경 중 이
    한 가지만 응답이 다릅니다.

    :returns: (pet id 들, 구성이 바뀌었나)
    """
    rows = await member_repo.list_bundle(session, invite.id)
    if not rows:
        return [invite.pet_id], False
    return [row.pet_id for row in rows], len(rows) != invite.pet_count


@dataclass(frozen=True)
class InvitePreview:
    """수락 전에 보여 줄 것. **건강정보는 없습니다** (MVP 결정 §8).

    아직 구성원이 아닌 사람에게 지병·상시 복용약을 내보이면, 토큰 하나로 남의 집 의료
    정보를 읽는 자리가 됩니다. 이름·견종·사진 있음 여부까지가 "이 아이가 맞나" 를 사람이
    판단하는 데 필요한 전부입니다.
    """

    invited_by_nickname: str | None
    expires_at: datetime
    #: (pet, 이미 구성원인가)
    pets: list[tuple[Pet, bool]]
    #: 내가 고를 수 있는 기존 강아지들.
    link_candidates: list[Pet]


async def preview_invite(
    session: AsyncSession, app_user_id: uuid.UUID, token: str
) -> InvitePreview:
    """수락 전 미리보기 + 연결 후보 (MVP 결정 §8).

    **하나의 응답인 이유** — 앱 화면이 "강아지 목록 + 각 줄의 연결 드롭다운" 한 장이라,
    나누면 왕복 두 번에 두 응답의 정합성을 앱이 맞춰야 합니다.

    **읽기만 합니다.** 만료건을 여기서 지우지 않습니다 — 지우면 그 뒤의 `accept` 재시도가
    404 를 받아 "만료" 와 "없는 토큰" 이 뭉개집니다. 청소는 수락과 새 초대 발급이 합니다.

    검증은 수락과 **같은 순서**입니다. 미리보기가 통과한 것이 수락에서 막히면 사용자가
    이유를 알 길이 없기 때문입니다.
    """
    invite = await member_repo.get_invite_by_hash(session, hash_refresh_token(token))
    if invite is None:
        raise InviteNotFoundError

    if invite.expires_at <= datetime.now(UTC):
        raise InviteExpiredError

    if invite.accepted_by is not None and invite.accepted_by != app_user_id:
        # 다른 사람이 이미 쓴 토큰. 존재했다는 사실 자체를 안 새게 404 입니다.
        raise InviteNotFoundError

    pet_ids, changed = await bundle_pet_ids(session, invite)
    if changed:
        raise InviteBundleChangedError

    found = await pet_repo.by_ids(session, pet_ids)
    if len(found) != len(pet_ids):
        # 자식 줄은 남았는데 강아지가 없는 경우는 FK CASCADE 상 나올 수 없지만,
        # 나오면 부분 수락이 되므로 묶음 전체를 무효로 봅니다.
        raise InviteBundleChangedError

    pets: list[tuple[Pet, bool]] = []
    for pet_id in pet_ids:
        pet = found[pet_id]
        common = await identity_service.common_of(session, pet)
        if invite.invited_by != common.app_user_id or common.farewell_on is not None:
            # 그새 주보호자가 바뀌었거나 배웅했습니다. 묶음 전체가 무효입니다.
            raise InviteBundleChangedError
        pets.append((pet, await member_repo.is_member(session, pet.id, app_user_id)))

    names = await app_user_repo.nicknames_by_ids(session, [invite.invited_by])
    return InvitePreview(
        invited_by_nickname=names.get(invite.invited_by),
        expires_at=invite.expires_at,
        pets=pets,
        link_candidates=await pet_repo.list_link_candidates(session, app_user_id),
    )


#: 항목별 수락 결과. 앱이 화면 문구를 이 값으로 가릅니다 (MVP 결정 §8).
ACCEPT_LINKED = "linked"
ACCEPT_JOINED = "joined"
ACCEPT_ALREADY_MEMBER = "already_member"
ACCEPT_ALREADY_OWNER = "already_owner"


class LinkNotAllowedError(Exception):
    """앱이 고른 연결 대상이 후보 조건을 안 지킵니다 (MVP 결정 §2).

    **서버가 다시 봅니다** — 미리보기가 후보를 내려 줬다고 그 목록을 믿으면, 그 사이
    상태가 바뀌었거나 앱이 임의의 id 를 넣은 것을 못 잡습니다.

    `reason` 은 앱이 문구를 가르는 기계용 값입니다. **남의 pet id 는 여기 안 옵니다** —
    그건 404 입니다(존재를 확인해 주지 않습니다).
    """

    def __init__(self, pet_id, link_to_pet_id, reason: str) -> None:
        self.pet_id = pet_id
        self.link_to_pet_id = link_to_pet_id
        self.reason = reason
        super().__init__(reason)


class InvalidLinkRequestError(Exception):
    """요청 자체가 앞뒤가 안 맞습니다 — 초대에 없는 `pet_id`, 또는 같은 연결 대상을 두 번.

    라우터가 422 로 바꿉니다. 409(상태 충돌)와 가르는 이유는 이쪽이 **서버 상태와
    무관하게** 틀린 요청이기 때문입니다. `code` 는 앱이 문구를 가르는 기계용 값입니다.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class AcceptedPet:
    """수락 결과 한 줄 (MVP 결정 §8).

    `invited_pet_id` 와 `display_pet_id` 를 **반드시 갈라 둡니다** — 연결했으면 이후 앱이
    쓸 id 는 초대에 담겼던 아이가 아니라 **받는 사람 자신의 행**입니다. 하나로 뭉치면
    B 가 A 의 행에 케어를 기록하게 됩니다.
    """

    #: 초대 묶음에 담겨 있던 원본 pet 행.
    invited_pet_id: uuid.UUID
    #: 받는 사람 화면과 **이후 요청**에 쓸 pet 행. 연결 안 했으면 위와 같습니다.
    display_pet_id: uuid.UUID
    #: 표시용 행의 이름.
    name: str
    #: `linked` · `joined` · `already_member` · `already_owner`
    result: str


@dataclass(frozen=True)
class AcceptOutcome:
    """묶음 하나의 수락 결과 전부."""

    #: 구 앱 호환 앵커 — 최상위 `pet_id`·`name` 이 여기서 나옵니다.
    anchor: AcceptedPet
    pets: list[AcceptedPet]


class _AnchorRow:
    """자식 줄이 없는 옛 초대의 영수증을 그릴 때만 쓰는 최소 대역."""

    def __init__(self, pet_id: uuid.UUID) -> None:
        self.pet_id = pet_id
        self.linked_pet_id: uuid.UUID | None = None


async def _bundle_rows(session: AsyncSession, invite: PetInvite):
    """묶음 줄을 **없으면 만들어서** 돌려줍니다.

    마이그레이션이 서버보다 먼저 나가는 창(MVP 결정 §9)에서 옛 코드가 만든 초대에는 자식
    줄이 없습니다. 그 줄이 없으면 `linked_pet_id` 영수증을 쓸 자리도 없어, 연결해서
    수락한 뒤의 재시도가 그때의 매핑을 복원하지 못합니다 — 그래서 여기서 채웁니다.
    """
    rows = await member_repo.list_bundle(session, invite.id)
    if rows:
        return rows
    rows = member_repo.add_invite_pets(session, invite.id, [invite.pet_id])
    await session.flush()
    return rows


def _validate_link_request(links: dict, bundle_ids: list) -> None:
    """요청이 이 묶음과 앞뒤가 맞는지. **아무것도 쓰기 전에** 봅니다.

    | 무엇 | 코드 | 상태 |
    | --- | --- | --- |
    | 초대에 없는 `pet_id` | `unknown_invited_pet` | 422 |
    | 같은 연결 대상을 두 번 | `duplicate_link_target` | 422 |
    | **묶음인데 선택이 빠짐** | `link_selection_required` | 409 |

    앞 둘은 서버 상태와 무관하게 틀린 요청이라 422 이고, 마지막은 **이 초대가 묶음이라서**
    생기는 조건이라 409 입니다 (`LinkSelectionRequiredError` 독스트링).
    """
    unknown = set(links) - set(bundle_ids)
    if unknown:
        raise InvalidLinkRequestError(
            "unknown_invited_pet", "초대에 없는 강아지가 요청에 있습니다."
        )

    targets = [target for target in links.values() if target is not None]
    if len(set(targets)) != len(targets):
        # 기존 강아지 하나를 초대 강아지 둘에 연결하는 것. DB 의
        # `pets_identity_one_per_user` 가 마지막 방어지만, 여기서 걸러야 이유를 말해 줍니다.
        raise InvalidLinkRequestError(
            "duplicate_link_target", "같은 강아지를 두 번 연결할 수 없습니다."
        )

    # **한 마리 묶음은 토큰만으로도 됩니다** — 고를 것이 하나뿐이고, 그것이 구 앱의
    # 계약입니다 (MVP 결정 §9). 두 마리부터는 전부 골라야 합니다.
    if len(bundle_ids) > 1:
        missing = [pet_id for pet_id in bundle_ids if pet_id not in links]
        if missing:
            raise LinkSelectionRequiredError(missing)


async def _receipt(session: AsyncSession, invite: PetInvite) -> "AcceptOutcome":
    """영수증으로 그때의 응답을 **그대로** 재구성합니다.

    강아지별 매핑이 `pet_invite_pets.linked_pet_id` 에 남아 있어, 묶음이어도 항목마다 같은
    `display_pet_id` 를 돌려줍니다. 새로 아무것도 확인하지 않습니다 — "응답을 못 받은
    재시도" 는 거의 곧바로 다시 오는 것을 전제합니다.
    """
    rows = await member_repo.list_bundle(session, invite.id) or [_AnchorRow(invite.pet_id)]
    display_ids = [row.linked_pet_id or row.pet_id for row in rows]
    pets = await pet_repo.by_ids(session, display_ids)
    items = []
    for row in rows:
        display_id = row.linked_pet_id or row.pet_id
        found = pets.get(display_id)
        items.append(
            AcceptedPet(
                invited_pet_id=row.pet_id,
                display_pet_id=display_id,
                name=found.name if found is not None else "",
                result=ACCEPT_LINKED if row.linked_pet_id else ACCEPT_JOINED,
            )
        )
    anchor = next((i for i in items if i.invited_pet_id == invite.pet_id), items[0])
    return AcceptOutcome(anchor=anchor, pets=items)


async def _group_guardian_count(
    session: AsyncSession, pet: Pet, joiner_id: uuid.UUID
) -> int:
    """이 사람이 들어온 **뒤**의 그룹 보호자 수 (중복 제거).

    연결 안 된 아이는 `대표 + 돌보미` 이고, 연결된 아이는 그룹 전체의 합집합입니다 —
    그래야 "한 논리 강아지의 보호자 최대 5명" 이 성립합니다 (MVP 결정 §4). pet 행별로
    세면 연결할 때마다 그룹 인원이 상한을 넘어 늘어납니다.
    """
    if pet.identity_id is not None:
        guardians = await identity_repo.guardian_ids(session, pet.identity_id)
    else:
        guardians = {pet.app_user_id, *await member_repo.list_members(session, pet.id)}
    return len(guardians | {joiner_id})


async def _require_link_candidate(session: AsyncSession, invited_pet_id, target: Pet) -> None:
    """연결 대상이 후보 조건을 지키는지 **서버가 다시 봅니다** (MVP 결정 §8).

    소유권(내 행인가)은 부르는 쪽이 이미 봤습니다 — 그건 404 라 여기 안 옵니다.
    """
    if target.identity_id is not None:
        raise LinkNotAllowedError(invited_pet_id, target.id, "already_linked")
    if target.farewell_on is not None:
        raise LinkNotAllowedError(invited_pet_id, target.id, "farewelled")
    if await member_repo.list_members(session, target.id):
        # 남의 기록이 이미 얹힌 아이입니다. 연결하면 그 공동 보호자의 케어·산책이
        # 동의 없이 새 그룹에 공개됩니다.
        raise LinkNotAllowedError(invited_pet_id, target.id, "has_other_carers")


async def accept_invite(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    token: str,
    links: dict | None = None,
) -> "AcceptOutcome":
    """묶음 **전체**를 한 번에 수락합니다 (MVP 결정 §2 · §8).

    **하나라도 실패하면 아무 변경도 남지 않습니다.** `session.commit()` 이 함수당 한 번이라
    예외가 나면 트랜잭션이 통째로 롤백됩니다 — 부분 수락이 구조적으로 불가능합니다.

    `links` 는 `{초대에 담긴 pet_id: 내 기존 pet_id}` 입니다. **선택 사항**이라 없으면 전부
    "연결 없이 참여" 이고, 그것이 구 앱 요청과 정확히 같은 동작입니다.

    검증 순서와 이유:

    1. **사용자 행을 먼저 잠급니다** — 마릿수 상한을 세는 단위가 사람입니다.
    2. 초대 강아지 ∪ 연결 대상을 **id 오름차순으로** 잠급니다. 한 요청이 여러 행을
       잠그므로 순서를 고정하지 않으면 데드락입니다.
    3. 만료 → 410 (행 삭제). 영수증이든 아니든 수명은 `expires_at` 까지입니다.
    4. **영수증** → 같은 사람이면 그때의 매핑을 그대로 200, 다른 사람이면 404.
    5. 묶음 구성 변경 → 410. **남은 강아지만 부분 수락하지 않습니다.**
    6. 요청 형식(초대에 없는 id · 연결 대상 중복) → 422
    7. 연결 대상의 **소유권 재검증** → 남의 것이면 404 (존재를 확인해 주지 않습니다)
    8. 연결 후보 조건 → 409 + `reason`
    9. 그룹 보호자 합집합 상한 → 409
    10. 수락 **후** 논리 강아지 수 → 409

    ⚠️ 영수증 분기는 그 사이 다른 일(탈퇴·내보내기)이 있었는지 다시 확인하지 않습니다.
    며칠 뒤에 같은 토큰으로 다시 들어오고 싶으면 새 초대를 받아야 합니다.
    """
    links = dict(links or {})

    invite = await member_repo.get_invite_by_hash(session, hash_refresh_token(token))
    if invite is None:
        raise InviteNotFoundError

    # 1. 사람 단위 직렬화. `create_pet`·`create_invite_bundle` 과 같은 이유입니다.
    await identity_service.lock_user(session, app_user_id)

    rows = await _bundle_rows(session, invite)
    bundle_ids = [row.pet_id for row in rows]

    # 2. 초대 강아지 ∪ 연결 대상을 한 번에, id 순으로.
    locked = await pet_repo.get_many_for_update(
        session, [*bundle_ids, *[t for t in links.values() if t is not None]]
    )

    now = datetime.now(UTC)
    if invite.expires_at <= now:
        await member_repo.delete_invite(session, invite.id)
        await session.commit()
        raise InviteExpiredError

    # 4. 영수증 — 새로 아무것도 확인하지 않습니다.
    if invite.accepted_by is not None:
        if invite.accepted_by == app_user_id:
            return await _receipt(session, invite)
        raise InviteNotFoundError

    # 5. 묶음 구성이 그대로인가.
    if len(rows) != invite.pet_count or any(pid not in locked for pid in bundle_ids):
        raise InviteBundleChangedError

    # 6. 요청 형식.
    _validate_link_request(links, bundle_ids)

    joined = 0
    items: list[AcceptedPet] = []
    for row in rows:
        pet = locked[row.pet_id]
        common = await identity_service.common_of(session, pet)

        # 그새 주보호자가 바뀌었거나 배웅했습니다. 묶음 전체가 무효입니다.
        if invite.invited_by != common.app_user_id or common.farewell_on is not None:
            raise InviteBundleChangedError

        if pet.app_user_id == app_user_id:
            items.append(AcceptedPet(row.pet_id, pet.id, pet.name, ACCEPT_ALREADY_OWNER))
            continue

        if await member_repo.is_member(session, pet.id, app_user_id):
            # 이미 충족된 항목입니다. 동시 수락에서 진 쪽도 여기로 옵니다 — 아래에서
            # 영수증을 채우므로 다음 재시도는 빠른 경로(4)로 들어옵니다.
            items.append(
                AcceptedPet(
                    row.pet_id,
                    row.linked_pet_id or pet.id,
                    pet.name,
                    ACCEPT_ALREADY_MEMBER,
                )
            )
            continue

        # 9. 그룹 보호자는 **합집합**으로 셉니다.
        if await _group_guardian_count(session, pet, app_user_id) > MAX_MEMBERS_PER_PET:
            raise MemberLimitError

        target_id = links.get(row.pet_id)
        if target_id is None:
            member_repo.add(session, pet.id, app_user_id)
            joined += 1
            items.append(AcceptedPet(row.pet_id, pet.id, pet.name, ACCEPT_JOINED))
            continue

        target = locked.get(target_id)
        # 7. **IDOR 방어.** 남의 id 는 404 입니다 — 409 면 그 id 가 존재한다는 것이 샙니다.
        if target is None or target.app_user_id != app_user_id:
            raise PetNotFoundError
        await _require_link_candidate(session, row.pet_id, target)

        member_repo.add(session, pet.id, app_user_id)
        await identity_service.link(session, pet, target)
        row.linked_pet_id = target.id
        items.append(AcceptedPet(row.pet_id, target.id, target.name, ACCEPT_LINKED))

    # 묶음 **전부**가 "이미 내 아이" 면 할 일이 하나도 없습니다 → 409.
    #
    # 항목이 섞여 있을 때는 `already_owner` 를 **충족된 항목**으로 보고 나머지를 진행하는
    # 것이 제품 규칙입니다 (MVP 결정 §2). 그런데 한 마리 초대에서 그 하나가 내 아이면
    # 결과가 "아무 일도 안 일어난 200" 이 되어, 구 앱이 "수락 성공" 을 그립니다 — 옛 계약의
    # 409("이미 이 아이의 대표입니다")가 그 자리에 있던 이유입니다 (MVP 결정 §9).
    # 그래서 **전부 그럴 때만** 409 로 두어 두 규칙을 다 지킵니다.
    if items and all(item.result == ACCEPT_ALREADY_OWNER for item in items):
        raise AlreadyOwnerError

    # 10. 수락 **후** 논리 강아지 수. 연결한 것은 안 늘어나므로 `joined` 만 셉니다.
    if await pet_repo.count_accessible(session, app_user_id) + joined > MAX_PETS_PER_USER:
        raise PetLimitError

    invite.accepted_at = now
    invite.accepted_by = app_user_id

    # 등록한 강아지가 없던 사람은 첫 수락에서 대표 강아지를 얻습니다 —
    # 없으면 앱 첫 화면이 빕니다 (`services/pet.py` 의 등록 경로와 같은 규칙).
    user = await app_user_repo.get_by_id(session, app_user_id)
    if user is not None and user.primary_pet_id is None and items:
        user.primary_pet_id = items[0].display_pet_id

    await session.commit()
    log.info(
        "공동 돌봄 참여 (invite=%s, user=%s, pets=%d)", invite.id, app_user_id, len(items)
    )
    anchor = next((i for i in items if i.invited_pet_id == invite.pet_id), items[0])
    return AcceptOutcome(anchor=anchor, pets=items)


async def list_invites(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> list[PetInvite]:
    """**그 아이가 낀 묶음** 전부, 만든 순서대로 — 구 경로용입니다.

    **그룹 주보호자만.** 평문 토큰이 발급 응답에 한 번만 나오므로, 나중에 그 초대를 찾아
    취소하려면 이 목록이 유일한 길입니다. 해시도 토큰도 안 돌려줍니다(`InviteOut` 이 그
    둘을 아예 담지 않습니다).

    앵커가 아닌 아이로도 찾힙니다 — 묶음에 담긴 아이는 전부 그 묶음을 볼 수 있어야
    대표가 취소할 수 있습니다.
    """
    pet = await pet_repo.get_owned(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError
    await identity_service.require_group_owner(session, app_user_id, pet)
    return await member_repo.list_invites_with_pet(session, pet_id)


async def list_my_invites(
    session: AsyncSession, app_user_id: uuid.UUID
) -> tuple[list[PetInvite], dict[uuid.UUID, list[uuid.UUID]], dict[uuid.UUID, str]]:
    """**내가 보낸 묶음 전부** — 신설 경로 `GET /app/pet-invites` 입니다.

    강아지별 목록(구 경로)만 있으면 앱이 마릿수만큼 왕복해야 하고, 묶음이 어느 아이의
    목록에 속하는지도 애매합니다. 초대의 주체가 강아지가 아니라 **사람**이 되면서 사용자
    단위 목록이 자연스러운 자리가 됐습니다.

    담긴 강아지 이름까지 한 번에 돌려줍니다 — 그래야 앱이 "맥스·코코를 부른 링크" 를 그릴
    수 있습니다. 쿼리는 초대 수와 무관하게 셋입니다(목록 · 묶음 · 이름).

    :returns: (초대들, invite_id → pet id 들, pet_id → 이름)
    """
    invites = await member_repo.list_invites_for_inviter(session, app_user_id)
    bundles = await member_repo.list_bundles(session, [i.id for i in invites])
    # 자식 줄이 없는 옛 초대는 앵커 하나짜리로 봅니다 (`bundle_pet_ids` 와 같은 규칙).
    for invite in invites:
        bundles.setdefault(invite.id, [invite.pet_id])
    names = await pet_repo.names_by_ids(
        session, [pet_id for ids in bundles.values() for pet_id in ids]
    )
    return invites, bundles, names


async def cancel_invite(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID, invite_id: uuid.UUID
) -> None:
    """초대 취소 — 구 경로용. **묶음 전체가 사라집니다** (MVP 결정 §2).

    **그룹 주보호자만.** 돌보미·제3자는 강아지가 안 보이므로 404 이고, 연결된 아이의
    비그룹주보호자는 409 입니다. 남의 강아지의 초대 id 를 넣어도 404 —
    `delete_invite_for_inviter` 가 초대한 사람까지 같이 걸기 때문입니다.

    `pet_id` 는 권한을 보는 데 **그리고 URL 의 두 id 가 짝인지 확인하는 데** 씁니다 —
    그 아이가 담기지 않은 묶음이면 404 입니다. 묶음에서 그 아이만 빼는 일은 없습니다.
    """
    pet = await pet_repo.get_owned(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError
    await identity_service.require_group_owner(session, app_user_id, pet)

    deleted = await member_repo.delete_invite_with_pet(
        session, app_user_id, pet_id, invite_id
    )
    if deleted == 0:
        raise InviteNotFoundError
    await session.commit()


async def cancel_invite_bundle(
    session: AsyncSession, app_user_id: uuid.UUID, invite_id: uuid.UUID
) -> None:
    """묶음 취소 — 신설 경로 `DELETE /app/pet-invites/{invite_id}`.

    **초대한 사람만.** 없는 id·남의 초대·이미 없는 것이 전부 같은 404 라 정보가 안 샙니다.
    이미 수락된(영수증) 초대도 지울 수 있습니다 — 취소는 "이 행을 없앤다" 는 뜻일 뿐입니다.
    자식 줄은 FK CASCADE 가 같이 지웁니다.
    """
    deleted = await member_repo.delete_invite_for_inviter(session, app_user_id, invite_id)
    if deleted == 0:
        raise InviteNotFoundError
    await session.commit()


async def list_members(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> list[MemberOut]:
    """대표를 맨 앞에, 그다음 돌보미를 참여 순으로. **구성원만 볼 수 있습니다.**

    연결된 강아지는 **논리 그룹 전체**의 보호자를 한 사람당 한 번씩 돌려줍니다 (MVP 결정 §4).
    요청한 행 하나만 보면, 기존 강아지와 연결한 공동 보호자는 자기 카드 id(= 자기 행)로
    부르므로 **자기 자신만 대표로** 뜨고 그룹 주보호자와 다른 보호자가 사라집니다.

    대표(`is_owner`)는 행 대표가 아니라 **그룹 주보호자**(공통 행의 대표)입니다 —
    `routers/pet.py` 의 `is_group_owner` 와 같은 정의입니다. 목록을 볼 수 있다고 관리
    권한이 생기지는 않습니다 — 내보내기·승계·초대는 각자 `require_group_owner` 를 지납니다.
    """
    pet = await pet_repo.get_accessible(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError

    common = await identity_service.common_of(session, pet)
    rows = [pet]
    if pet.identity_id is not None:
        group = await identity_repo.pets_for(session, pet.identity_id)
        if group:
            # 그룹 주보호자의 행을 맨 앞에, 나머지는 등록 순서대로.
            rows = [common, *(p for p in group if p.id != common.id)]

    ordered: list[uuid.UUID] = [common.app_user_id]
    for row in rows:
        ordered.append(row.app_user_id)
        ordered += await member_repo.list_members(session, row.id)
    # 한 사람이 그룹 안에서 자기 행의 대표이면서 주보호자 행의 돌보미일 수 있습니다
    # (연결 수락이 둘을 같이 만듭니다). 처음 나온 자리만 남깁니다.
    guardians = list(dict.fromkeys(ordered))

    # 그룹으로 넓힌 명단은 **부른 사람이 그 명단에 있을 때만** 냅니다. 행 접근이 통과했으면
    # 늘 참이지만, 행 판정과 그룹 판정이 어긋나는 날 남의 그룹 명단이 새지 않게 둡니다.
    if app_user_id not in guardians:
        raise PetNotFoundError

    names = await app_user_repo.nicknames_by_ids(session, guardians)
    return [
        MemberOut(
            app_user_id=uid,
            nickname=names.get(uid),
            is_owner=uid == common.app_user_id,
        )
        for uid in guardians
    ]


async def remove_member(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    target_id: uuid.UUID,
) -> None:
    """내보내기(그룹 주보호자) 또는 나가기(본인). 같은 경로입니다.

    ⚠️ **멤버십과 논리 연결을 한 트랜잭션에서 같이 정리합니다** (MVP 결정 §6).
    멤버십만 지우고 연결이 남으면, 나간 사람의 pet 행이 그룹에 그대로 있어 케어·산책
    공동 조회로 **남의 집 기록을 계속 읽습니다.** 둘이 갈라지는 순간이 있으면 안 됩니다.

    내보내기는 **그룹 주보호자만** 합니다 — 연결된 아이에서 행 대표라는 것만으로 남을
    내보내면, 그룹의 주인이 아닌 사람이 그룹 구성을 바꾸게 됩니다. **그룹 주보호자가
    아닌 사람이 남을 지목하면 행 소유와 무관하게 전부 403 입니다** (`NotAllowedError`).

    ⚠️ **판단은 전부 논리 그룹 기준입니다. 요청한 행의 대표가 누구인지로 정하지 않습니다.**
    `pet_id` 로 오는 것은 부른 사람이 화면에서 쥐고 있는 id, 즉 **자기 표시용 행**
    (`display_pet_id`)입니다. 연결한 공동 보호자에게 그 행은 **자기가 대표인 자기 행**이라,
    행 대표로 판단하면 자기 자신을 지목한 나가기가 `CannotRemoveOwnerError` 로 막혔습니다 —
    앵커 행 id 로만 나갈 수 있었는데 앱은 그 id 를 알 방법이 없어 **나가기가 아예 불가능**
    했습니다. 그래서 대표 보호는 **공통 행의 대표(= 그룹 주보호자)** 에만 겁니다.

    지우는 범위도 그룹 전체입니다 — 한 사람이 그룹 안 여러 행의 돌보미일 수 있어
    (연결 수락이 앵커 행 멤버십을 같이 만듭니다) 요청한 행 하나만 지우면 다른 행의
    멤버십으로 그룹을 계속 읽습니다.
    """
    pet = await pet_repo.get_accessible(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError

    common = await identity_service.common_of(session, pet)

    # ① 권한. **남을 내보내는 것은 그룹 주보호자뿐이고, 아니면 전부 403 입니다.**
    #    `pet.app_user_id`(요청한 행의 대표)는 **안 봅니다** — 연결한 공동 보호자는 자기
    #    카드 행의 대표라, 행 소유로 가르면 같은 상황이 부른 id 에 따라 409 와 403 으로
    #    갈렸습니다. 행 소유는 사용자가 모르는 내부 사정입니다.
    if app_user_id != target_id and app_user_id != common.app_user_id:
        raise NotAllowedError

    # ② 대표 보호. ① 을 지났으므로 여기 걸리는 것은 **그룹 주보호자가 자기를 지목한 것**
    #    하나뿐입니다 (남이 주보호자를 지목한 것은 ① 에서 403 으로 끝났습니다). 승계로
    #    가라는 안내라, 자기 자신에게만 주는 것이 맞습니다.
    if target_id == common.app_user_id:
        raise CannotRemoveOwnerError

    # ⚠️ **연결을 풀기 전에** 그룹 행 id 를 뽑습니다 — `detach_user` 가 `identity_id` 를
    #    비우고 나면 같은 질문에 다른 답이 나옵니다.
    group_ids = await identity_service.group_pet_ids_of(session, pet)

    # IDOR 가드. 그룹의 보호자가 아닌 id 를 넣으면 **아무 일도 없이 204** 가 나가
    # "이 사람은 원래 없었다" 와 "지웠다" 가 같아 보였습니다. 그룹 밖 사람은 404 입니다 —
    # 그 사람이 존재하는지조차 안 알려줍니다.
    if target_id not in await identity_service.guardians_of(session, pet):
        raise PetNotFoundError

    # 그룹의 **모든 행**에서 지웁니다. 요청한 행 하나만 지우면 앵커 행 멤버십이 남습니다.
    await member_repo.remove_from_pets(session, group_ids, target_id)

    # 나간 사람의 행을 그룹에서 뗍니다. 혼자 남은 그룹은 `prune` 이 정리합니다.
    if pet.identity_id is not None:
        await identity_service.detach_user(session, pet.identity_id, target_id)

    # ⚠️ `primary_pet_id` 의 FK 는 ON DELETE SET NULL 이지만 **강아지 행은 안 지워지므로
    #    안 돕니다.** 여기서 명시로 비웁니다 — 안 그러면 접근 못 하는 아이를 가리킵니다.
    #
    # 그룹 행 **아무거나** 가리키고 있을 수 있어(앵커 행을 대표로 세워 둔 공동 보호자)
    # `pet_id` 하나만 보지 않습니다. 반대로 나간 사람이 **계속 볼 수 있는** 행(자기 행)을
    # 가리키고 있으면 건드리지 않습니다 — 멀쩡한 첫 화면을 흔들 이유가 없습니다.
    user = await app_user_repo.get_by_id(session, target_id)
    if user is not None and user.primary_pet_id in set(group_ids):
        remaining = await pet_repo.list_accessible(session, target_id)
        if all(p.id != user.primary_pet_id for p in remaining):
            user.primary_pet_id = remaining[0].id if remaining else None

    # commit 은 여기 한 번뿐입니다 — 멤버십 삭제·연결 해제·대표 수선이 **같은 트랜잭션**
    # 이라, 중간에서 터지면 셋 다 안 일어난 것이 됩니다 (`get_session` 은 commit 하지
    # 않고 빠져나갈 때 rollback 합니다).
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
    # 연결된 아이의 승계는 **그룹 주보호자만** 합니다 (MVP 결정 §6). 비그룹주보호자의
    # 행은 그 그룹에 돌보미가 없어 아래 `NotAMemberError` 로도 막히지만, 이유가 "돌보미가
    # 아니다" 로 나가면 앱이 엉뚱한 안내를 그립니다 — 여기서 먼저 정확한 이유를 냅니다.
    await identity_service.require_group_owner(session, app_user_id, pet)
    if new_owner_id == pet.app_user_id:
        # 대표가 자기 자신을 지목했습니다. `is_member` 는 대표도 True 로 치므로 이 검사가
        # 없으면 아래를 그대로 통과해 무의미한 UPDATE 뒤 `member_repo.add(self)` 에서
        # `pet_members_not_owner` 트리거가 터집니다(아직 안 잡히는 예외 → 500). 더 구체적인
        # 답이 이기도록 멤버십 검사보다 먼저 둡니다.
        raise AlreadyOwnerError
    if not await member_repo.is_member(session, pet_id, new_owner_id):
        raise NotAMemberError

    # ⚠️ **연결된 그룹에서 대상이 이미 자기 행을 갖고 있으면 막습니다.** 아래 ① 이 앵커
    #    행의 소유를 대상으로 옮기는데, 그러면 한 사람이 한 그룹에 행 둘을 갖게 되어
    #    `pets_identity_one_per_user` 부분 UNIQUE 를 위반합니다 — 안 막으면 500 입니다.
    #    (일회용 PostgreSQL 로 재현: duplicate key value violates unique constraint.)
    #    제대로 지원하려면 행 소유가 아니라 `owner_pet_id` 를 옮겨야 하는데, 그건 승계의
    #    의미를 둘로 가르는 제품 결정이라 MVP 에서 하지 않습니다.
    if pet.identity_id is not None and await identity_repo.pet_of_user(
        session, pet.identity_id, new_owner_id
    ):
        raise LinkedOwnerTransferError(pet.name)

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


async def group_actor_label(
    session: AsyncSession,
    pet_ids: list[uuid.UUID],
    app_user_id: uuid.UUID | None,
) -> str | None:
    """`actor_label` 의 **논리 그룹판** (MVP 결정 §7).

    왜 그냥 `actor_label` 로 안 되나 — 그것은 `pet_id` **하나**의 구성원인지를 봅니다.
    연결된 그룹에서는 A 가 자기 행(101)의 대표이고 B 가 자기 행(202)의 대표인데, **A 는
    202 의 구성원이 아닙니다.** 그래서 B 의 화면(202 기준)에서 A 가 적은 기록의 이름이
    통째로 비어 "이전 보호자" 로 그려집니다 — 같은 집 사람인데도요.

    그룹 안의 **어느 행에든** 구성원이면 이름을 냅니다. 연결이 없으면 `pet_ids` 가 하나라
    `actor_label` 과 정확히 같습니다.

    표시 규칙 자체는 그대로입니다 — **지금도** 구성원일 때만 이름이 나고, 탈퇴·내보내기로
    나간 사람은 여전히 `None` 입니다 (docs/co-care.md §3).
    """
    if app_user_id is None:
        return None
    for pet_id in pet_ids:
        if await member_repo.is_member(session, pet_id, app_user_id):
            names = await app_user_repo.nicknames_by_ids(session, [app_user_id])
            return names.get(app_user_id)
    return None


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
    "InviteBundleChangedError",
    "InviteExpiredError",
    "InviteLimitError",
    "InviteNotFoundError",
    "InvitePreview",
    "LinkSelectionRequiredError",
    "LinkedOwnerTransferError",
    "MemberLimitError",
    "NotAMemberError",
    "NotAllowedError",
    "PetFarewelledError",
    "PetLimitError",
    "accept_invite",
    "actor_label",
    "actor_labels",
    "bundle_pet_ids",
    "cancel_invite",
    "cancel_invite_bundle",
    "create_invite",
    "create_invite_bundle",
    "group_actor_label",
    "list_invites",
    "list_members",
    "list_my_invites",
    "preview_invite",
    "remove_member",
    "transfer_owner",
]
