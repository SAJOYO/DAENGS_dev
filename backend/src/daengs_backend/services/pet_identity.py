"""논리 강아지의 규칙 (MVP 결정 §3~§6).

여러 `pets` 행이 **같은 실제 강아지**일 때 무엇을 어느 행에서 읽고, 누가 무엇을 바꿀 수
있는지를 여기서 정합니다. 행을 합치거나 기록을 옮기는 코드는 이 파일에도 없습니다 —
**물리 병합을 하지 않는 것이 이 기능의 전제**입니다.

읽는 자리가 둘로 갈립니다:

* **표시용 행**(`PetView.display`) — 이름·프로필 사진·그리고 그 사용자가 이후 API 에 쓸
  `pet_id`. 각 보호자가 자기 행을 씁니다.
* **공통 행**(`PetView.common`) — 견종·성별·생일·몸무게·급식·지병·알레르기·상시 복용약·
  배웅 상태. 언제나 그룹 주보호자의 행(`pet_identities.owner_pet_id`)입니다.

연결 전 각 행에 있던 값은 **덮어쓰지 않습니다.** 읽을 때만 주보호자 쪽을 보고, 연결이
풀리면 각자의 원본 값이 그대로 돌아옵니다.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Pet
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import pet_identity as identity_repo

__all__ = [
    "NotGroupOwnerError",
    "PetView",
    "collapse",
    "common_of",
    "detach_user",
    "group_pet_ids_of",
    "link",
    "lock_user",
    "require_group_owner",
    "view_of",
]


class NotGroupOwnerError(Exception):
    """연결된 강아지의 **그룹 관리 동작**을 그룹 주보호자가 아닌 사람이 불렀습니다.

    행 대표(`pets.app_user_id`)라는 것만으로는 부족한 자리입니다 — 초대·내보내기·승계·
    삭제·배웅·공통 프로필 수정이 그것입니다 (MVP 결정 §6). B 가 자기 `pets` 행의 대표라도
    그룹의 주보호자는 A 이므로, B 가 그 행을 지우면 **그룹의 기록 한쪽이 통째로 사라집니다.**

    라우터가 **409** 로 바꿉니다. 404 가 아닌 이유는 호출자가 그 강아지를 이미 자기
    목록에서 보고 있어 감출 것이 없기 때문입니다 — 404 면 앱이 "강아지를 찾을 수 없습니다"
    라고 거짓말을 그립니다.
    """

    def __init__(self, pet_name: str) -> None:
        self.pet_name = pet_name
        super().__init__(pet_name)


@dataclass(frozen=True)
class PetView:
    """화면에 뜨는 강아지 한 마리. **행이 둘일 수 있습니다.**

    연결 안 된 강아지는 `display is common` 입니다 — 그때 이 구조는 아무것도 안 바꿉니다.
    """

    #: 이름·사진과 **이후 API 에 쓸 `pet_id`**. 그 사용자의 행입니다.
    display: Pet
    #: 견종·생일·지병·배웅 등 공통 정보. 그룹 주보호자의 행입니다.
    common: Pet

    @property
    def group_owner_id(self) -> uuid.UUID:
        """이 그룹의 주보호자 사용자 id. **공통 행의 대표가 곧 그룹 주보호자입니다** —
        주보호자를 따로 저장하지 않는 이유가 이것입니다 (`models/pet_identity.py`)."""
        return self.common.app_user_id


async def lock_user(session: AsyncSession, app_user_id: uuid.UUID) -> None:
    """상한을 세기 전에 **사용자 행을 잠급니다** (MVP 결정 §4 · §8).

    `pets` 행만 잠그면 모자랍니다 — 서로 다른 강아지를 겨냥한 동시 요청은 서로 다른 행을
    잠그므로 둘 다 "아직 4마리" 를 보고 통과해 5마리를 넘깁니다. 세는 단위가 사용자이므로
    잠그는 단위도 사용자여야 합니다.

    **`CurrentAppUser` 의존성이 이미 같은 행을 잠그고 있습니다**(`core/deps.py`). 그래도
    여기서 다시 부르는 이유는 상한이라는 도메인 불변식을 **인증 계층에 기대면 안 되기**
    때문입니다 — 누군가 라우터를 `CurrentAppMemberTokenOnly` 로 바꾸면 그 순간 직렬화가
    조용히 사라집니다. 같은 트랜잭션이 같은 행을 다시 잠그는 것은 공짜입니다.

    탈퇴한 회원이면 아무 행도 안 잠깁니다 — 그 경우의 401 은 이미 의존성이 냅니다.
    """
    await app_user_repo.get_active_for_update(session, app_user_id)


async def common_of(session: AsyncSession, pet: Pet) -> Pet:
    """공통 정보를 읽을 행. 연결 안 됐으면 자기 자신입니다.

    ⚠️ **자기 자신을 돌려주는 것이 기본값입니다.** 그룹 행이나 앵커 행이 없어진 드문
    상태(동시 삭제)에서도 화면이 비지 않도록, 못 찾으면 조용히 자기 행으로 떨어집니다 —
    그 순간 보이는 값은 "연결 전" 값이라 틀린 값이 아닙니다.
    """
    if pet.identity_id is None:
        return pet
    identities = await identity_repo.get_many(session, [pet.identity_id])
    identity = identities.get(pet.identity_id)
    if identity is None:
        return pet
    if identity.owner_pet_id == pet.id:
        return pet
    owners = await pet_repo.by_ids(session, [identity.owner_pet_id])
    return owners.get(identity.owner_pet_id, pet)


async def view_of(session: AsyncSession, pet: Pet) -> PetView:
    """한 마리의 표시용·공통 행 짝."""
    return PetView(display=pet, common=await common_of(session, pet))


async def collapse(
    session: AsyncSession, app_user_id: uuid.UUID, pets: list[Pet]
) -> list[PetView]:
    """접근 가능한 `pets` 행을 **논리 강아지당 한 장**으로 접습니다 (MVP 결정 §4).

    고르는 규칙은 하나입니다:

    > 같은 그룹 안에 **내가 대표인 행**이 있으면 그 행이 내 카드다. 없으면 그룹의
    > `owner_pet_id` 행이다.

    그래서 A 의 `롱이씨`와 B 의 `롱롱씨`를 연결하면 A 는 `롱이씨` 한 장, B 는 `롱롱씨`
    한 장을 봅니다. 같은 사람이 한 그룹에 두 행을 가질 수 없는 것은
    `pets_identity_one_per_user` 부분 UNIQUE 가 DB 에서 보장합니다 — 그래서 "내가 대표인
    행" 은 많아야 하나입니다.

    정렬은 입력 순서(`created_at, id`)를 그대로 씁니다. 그룹의 대표 행을 고를 때도 그
    그룹이 목록에 처음 나타난 자리를 유지하므로, 초대를 수락해도 기존 카드가 재배열되지
    않습니다.
    """
    identity_ids = {p.identity_id for p in pets if p.identity_id is not None}
    identities = await identity_repo.get_many(session, list(identity_ids))

    by_id = {p.id: p for p in pets}
    missing = [
        i.owner_pet_id for i in identities.values() if i.owner_pet_id not in by_id
    ]
    # 앵커 행이 접근 가능한 목록에 없는 것은 정상이 아닙니다(구성원이면 보여야 합니다).
    # 동시 삭제·정리 중의 짧은 틈에서만 생기므로, 그때만 한 번 더 읽어 화면이 비지 않게
    # 합니다.
    if missing:
        by_id |= await pet_repo.by_ids(session, missing)

    views: list[PetView] = []
    seen: set[uuid.UUID] = set()
    for pet in pets:
        key = pet.identity_id or pet.id
        if key in seen:
            continue

        if pet.identity_id is None:
            views.append(PetView(display=pet, common=pet))
            seen.add(key)
            continue

        group = [p for p in pets if p.identity_id == pet.identity_id]
        display = next((p for p in group if p.app_user_id == app_user_id), None)
        identity = identities.get(pet.identity_id)
        common = by_id.get(identity.owner_pet_id) if identity is not None else None
        if common is None:
            common = display or group[0]
        if display is None:
            display = common
        views.append(PetView(display=display, common=common))
        seen.add(key)
    return views


async def require_group_owner(
    session: AsyncSession, app_user_id: uuid.UUID, pet: Pet
) -> Pet:
    """그룹 관리 동작의 공통 가드. 통과하면 **공통 행**을 돌려줍니다.

    연결 안 된 강아지는 행 대표가 곧 그룹 주보호자라 아무것도 안 바뀝니다 — 그래서 이
    가드를 기존 경로에 얹어도 지금까지의 동작이 그대로입니다.

    부르는 쪽은 **먼저 `pet_repo.get_owned` 로 행 대표인지 확인한 뒤** 이것을 부릅니다.
    순서가 반대면 남의 강아지에 대해 "그룹 주보호자가 아니다" 라고 알려 주게 됩니다.
    """
    common = await common_of(session, pet)
    if common.app_user_id != app_user_id:
        raise NotGroupOwnerError(common.name)
    return common


async def group_pet_ids_of(session: AsyncSession, pet: Pet) -> list[uuid.UUID]:
    """케어·산책 **공동 조회**가 읽을 pet id 전부 (MVP 결정 §7).

    연결 안 된 강아지는 `[pet.id]` 하나라, 이 함수를 끼워도 지금 동작이 안 바뀝니다.

    ⚠️ **`pet_repo.member_condition` 을 넓히는 대신 이것을 씁니다.** 저것은 chat·
    gait_record·screening·territory_claim·walk_entry 다섯이 같이 쓰므로, 거기를 그룹으로
    넓히면 이번 MVP 가 **보류한** 보행·스크리닝·대화·점령까지 조용히 공유됩니다.
    여기를 다른 도메인이 가져다 쓰기 전에 그 도메인이 공유 범위에 들어왔는지 먼저 보세요.

    부르는 쪽이 **이미 접근 권한을 확인한 `pet`** 을 넘깁니다 — 앱이 보낸 id 목록을
    그대로 받는 자리를 안 만들어야 IDOR 이 생기지 않습니다.
    """
    if pet.identity_id is None:
        return [pet.id]
    ids = await identity_repo.pet_ids_for(session, pet.identity_id)
    return ids or [pet.id]


async def link(session: AsyncSession, invited: Pet, target: Pet) -> uuid.UUID:
    """받는 사람의 `target` 행을 초대된 `invited` 행과 **같은 실제 강아지**로 잇습니다.

    `invited` 가 아직 그룹이 없으면 여기서 만들고 **그 행을 앵커로 둡니다** — 그룹의
    주보호자는 초대한 쪽이라는 제품 규칙이 그렇게 표현됩니다 (MVP 결정 §1).

    행을 지우거나 값을 옮기지 않습니다. `identity_id` 두 칸만 채웁니다.

    :returns: 그룹 id
    """
    if invited.identity_id is None:
        identity = identity_repo.add(session, invited.id)
        # id 가 있어야 아래 두 행에 채울 수 있습니다. DB 기본값을 미리 받아 옵니다.
        await session.flush()
        invited.identity_id = identity.id
    target.identity_id = invited.identity_id
    return invited.identity_id


async def detach_user(
    session: AsyncSession, identity_id: uuid.UUID, app_user_id: uuid.UUID
) -> None:
    """그 사용자의 행을 그룹에서 뗍니다. 나가기·내보내기가 부릅니다 (MVP 결정 §6).

    **멤버십만 지우고 이것을 안 하면** 그 사람의 행이 그룹에 남아 공동 조회로 계속
    기록을 읽습니다 — 나간 사람이 남의 집 케어·산책을 계속 보는 상태입니다. 그래서 둘은
    같은 트랜잭션에서 함께 일어나야 합니다.

    앵커 행(그룹 주보호자의 행)은 여기서 떼지 않습니다 — 그룹 주보호자는 이 경로로 못
    나갑니다(`CannotRemoveOwnerError`). 뗀 뒤에는 `prune` 이 남은 수를 봅니다.
    """
    for pet in await identity_repo.pets_for(session, identity_id):
        if pet.app_user_id == app_user_id:
            pet.identity_id = None
    await prune(session, identity_id)


async def prune(session: AsyncSession, identity_id: uuid.UUID) -> None:
    """혼자 남은 그룹을 정리합니다.

    pet 행이 하나 이하로 줄면 "여럿이 같은 아이" 라는 뜻이 없어집니다. 남은 행의
    `identity_id` 를 비우고 그룹 행을 지워, **연결 이전과 똑같은 모양**으로 되돌립니다 —
    그 행의 이름·사진·견종·지병은 처음부터 자기 것이었으므로 복원할 값이 없습니다.
    """
    remaining = await identity_repo.pets_for(session, identity_id)
    if len(remaining) > 1:
        return
    for pet in remaining:
        pet.identity_id = None
    await identity_repo.delete(session, identity_id)
