"""강아지 프로필의 규칙. 트랜잭션 경계도 여기입니다.

라우터는 HTTP 만 보고, 리포지토리는 쿼리만 합니다. "몇 마리까지"·"대표를 누구로"
같은 판단은 전부 여기 모입니다.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.storage import UploadTicket, build_pet_photo_key, get_storage
from daengs_backend.models import AppUser, Pet
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import pet_member as member_repo
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.schemas.pet import PetUpsert
from daengs_backend.services import gait as gait_service
from daengs_backend.services import pet_identity as identity_service

log = logging.getLogger(__name__)

#: 한 계정에 등록할 수 있는 마릿수.
#:
#: ⚠️ **임의값입니다.** 앱 쪽에서 정한 기준은 "몇 마리가 자연스러운가"가 아니라
#: **미니룸에 몇 마리까지 담기나** 입니다 — 등록한 강아지가 방을 돌아다니게 할
#: 계획이라, 12×12 격자에 가구가 차 있고 강아지 간격이 1.3칸인 것이 상한을 정합니다.
#: 실기기에서 세워 보고 정하기로 했고, 그 결과를 여기 한 줄만 고치면 됩니다.
MAX_PETS_PER_USER = 5

#: 프로필 사진 한 장의 상한(바이트).
#:
#: 얼굴 한 장이라 점령지 증거 사진(12 MiB)만큼 클 이유가 없습니다. 그리고 이 경로에는
#: **nginx 의 전용 블록이 없어서** server 기본값 20m 아래에 있습니다 — 그보다 낮게
#: 두어야 앱이 nginx 의 HTML 대신 우리 413 을 받습니다.
MAX_PET_PHOTO_BYTES = 8 * 1024 * 1024

#: 앱이 그릴 수 있는 형식. DB CHECK 와 같은 목록입니다.
PET_PHOTO_CONTENT_TYPES = ("image/jpeg", "image/webp")

#: 사진 bridge 의 경로. 보행·점령지처럼 도메인마다 다릅니다.
PET_PHOTO_BRIDGE_UPLOAD_PATH = "/app/pets/_bridge/upload"
PET_PHOTO_BRIDGE_DOWNLOAD_PATH = "/app/pets/_bridge/download"


class PetLimitReachedError(Exception):
    """마릿수 상한. 라우터가 409 로 바꿉니다."""


class PetPhotoConflictError(Exception):
    """사진 상태가 요청과 안 맞습니다. 라우터가 409 로 바꿉니다.

    ``code`` 는 앱이 분기할 수 있게 기계용으로, ``detail`` 은 사람에게 보여 줄
    문장으로 나눠 둡니다 (점령지의 같은 예외와 같은 모양).
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class PetNotFoundError(Exception):
    """내 강아지가 아니거나 없습니다.

    **남의 것일 때도 이 예외입니다.** 403 으로 나누면 "그 id 는 존재한다"를
    알려 주는 셈이라, 없는 것과 남의 것을 같은 404 로 뭉갭니다.
    """


class OwnerHasCarersError(Exception):
    """돌보미가 남은 강아지의 대표는 그냥 탈퇴할 수 없습니다 (docs/co-care.md §3).

    막는 것이 목적이 아니라 **순서를 요구하는 것**입니다 — 대표를 넘기거나 돌보미를
    내보내면 바로 탈퇴할 수 있습니다. 라우터가 이 두 출구를 메시지에 같이 담아
    409 로 바꿉니다.
    """

    def __init__(self, pet_names: list[str]) -> None:
        self.pet_names = pet_names
        super().__init__(", ".join(pet_names))


class PetHasCarersError(Exception):
    """돌보미가 남은 강아지는 확인 없이 지울 수 없습니다 (docs/co-care.md §3, Task 13).

    **`OwnerHasCarersError`(탈퇴 가드)와 다른 메커니즘입니다.** 탈퇴는 강아지를
    파괴하는 것이 *부수효과*라 하드 블록에 출구 두 개를 안내합니다 — 사람이 강아지를
    생각하고 있지 않기 때문입니다. 삭제는 대표가 그 강아지를 **겨냥**한 행동이라 무엇을
    할지는 이미 알고 있고, 모르는 것은 "누가 돌보고 있는가" 뿐입니다. 그래서 여기는
    막지 않고 **확인만** 요구합니다 — `confirm=True` 로 다시 부르면 그대로 지웁니다.
    """

    def __init__(self, pet_name: str, carers: list[tuple[uuid.UUID, str | None]]) -> None:
        self.pet_name = pet_name
        #: (app_user_id, nickname) 목록. 라우터가 그대로 409 본문의 `carers` 로 내보냅니다.
        self.carers = carers
        super().__init__(pet_name)


async def list_pets(
    session: AsyncSession, app_user_id: uuid.UUID
) -> tuple[list[identity_service.PetView], uuid.UUID | None]:
    """**내가 돌보는 아이 전부**와 대표 id. 대표는 계정 쪽에 있어서 같이 읽어 옵니다.

    구성원 기준입니다 (docs/co-care.md §2) — 대표만 돌려주면 돌보미의 `GET /app/pets`
    가 비어서, 초대를 수락해도 앱에 그 아이가 아예 안 보입니다. 기록·조회는 열어 놓고
    목록만 닫으면 기능이 있는데 못 찾는 상태가 됩니다.

    누가 대표인지는 응답의 `is_owner` 로 갈립니다 — 앱이 수정·삭제 버튼을 그 값으로
    가립니다 (`routers/pet.py::_to_response`).

    **논리 강아지당 카드 한 장으로 접습니다** (MVP 결정 §4). 같은 실제 강아지를 A 와 B 가
    각자 등록해 연결하면 B 의 접근 가능 목록에는 행이 둘(자기 202, 돌보미로 101)이지만
    카드는 `롱롱씨` 한 장입니다 — 접지 않으면 마이에 카드 두 장, 미니룸에 두 마리가 그대로
    뜹니다. 고르는 규칙과 공통 정보 투영은 `services/pet_identity.py::collapse` 입니다.
    """
    pets = await pet_repo.list_accessible(session, app_user_id)
    views = await identity_service.collapse(session, app_user_id, pets)
    user = await app_user_repo.get_by_id(session, app_user_id)
    return views, (user.primary_pet_id if user else None)


async def create_pet(
    session: AsyncSession, app_user_id: uuid.UUID, body: PetUpsert
) -> tuple[Pet, uuid.UUID | None]:
    """등록. **첫 아이는 자동으로 대표가 됩니다.**

    고르라고 묻지 않는 이유는 고를 것이 없어서입니다. 두 마리째부터 사용자가 정합니다.

    대표 id 를 같이 돌려주는 이유는 라우터가 응답을 만들 때 필요해서입니다 —
    안 주면 라우터가 목록을 한 번 더 읽어야 합니다.
    """
    # **구성원 기준으로 셉니다** (docs/co-care.md §2). 상한은 남용 한도가 아니라 미니룸
    # 렌더링 제약이라(위 MAX_PETS_PER_USER 주석), 세는 대상은 소유가 아니라 "내 방에 서는
    # 아이 수" 여야 합니다. 소유로 세면 돌보미로 5마리를 받은 사람이 자기 강아지를 계속
    # 등록해 방이 넘칩니다. **승계는 다릅니다** — 거기서 늘어나는 것은 소유뿐이라
    # `count_for_owner` 로 봅니다 (Task 7).
    #
    # 세기 **전에 사용자 행을 잠급니다** (MVP 결정 §4). 등록과 초대 수락이 동시에 오면
    # 서로 다른 pet 행을 건드리므로 pet 잠금으로는 직렬화가 안 되고, 둘 다 "아직 4마리"
    # 를 보고 통과합니다. 세는 단위가 사용자라 잠그는 단위도 사용자여야 합니다.
    await identity_service.lock_user(session, app_user_id)
    if await pet_repo.count_accessible(session, app_user_id) >= MAX_PETS_PER_USER:
        raise PetLimitReachedError

    pet = Pet(app_user_id=app_user_id, **body.model_dump())
    pet_repo.add(session, pet)
    # id 가 있어야 대표로 세울 수 있습니다. commit 전에 DB 기본값을 받아 옵니다.
    await session.flush()

    user = await app_user_repo.get_by_id(session, app_user_id)
    if user is not None and user.primary_pet_id is None:
        user.primary_pet_id = pet.id

    await session.commit()
    # refresh 하지 않습니다 — 세션이 `expire_on_commit=False` 라 commit 뒤에도
    # 속성을 그대로 읽습니다 (core/database.py). 부르면 왕복만 한 번 더 늡니다.
    return pet, (user.primary_pet_id if user is not None else None)


async def update_pet(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID, body: PetUpsert
) -> Pet:
    """프로필 **전체** 수정. 배웅(`farewell_on`)도 이 한 요청에 실려 옵니다.

    ⚠️ **연결된 강아지는 그룹 주보호자만** 부를 수 있습니다 (MVP 결정 §5 · §6).

    이 계약은 전체 PUT 이라, 연결된 B 가 이름만 고치려고 불러도 **A 의 공통 정보**(견종·
    생일·지병·배웅)가 그대로 B 의 원본 행에 덮어써집니다 — 앱이 화면에 그린 값은 투영된
    A 의 값이기 때문입니다. 공통 필드를 조용히 무시하는 것도, 원본을 덮어쓰는 것도 답이
    아니므로 **막고 이유를 말합니다**(409). 이름만 바꾸는 길은 `update_display` 입니다.

    연결 안 된 강아지에서는 행 대표가 곧 그룹 주보호자라 지금까지와 똑같이 동작합니다.
    """
    pet = await pet_repo.get_owned(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError
    await identity_service.require_group_owner(session, app_user_id, pet)

    for field, value in body.model_dump().items():
        setattr(pet, field, value)

    await session.commit()
    return pet


async def update_display(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID, *, name: str
) -> Pet:
    """**이름만** 바꿉니다. 행 대표면 됩니다 (MVP 결정 §5).

    이름·사진은 보호자마다 자기 값이라, 연결된 B 도 자기 행의 `롱롱씨` 를 계속 고칠 수
    있어야 합니다. 전체 PUT 이 연결된 행에서 409 인 것과 짝입니다 — 좁은 계약 하나를 여는
    쪽이, 넓은 계약에서 공통 필드만 몰래 무시하는 것보다 낫습니다.

    그룹 주보호자 검사를 **안 합니다.** 여기서 바뀌는 것은 그 사람 행의 이름 한 칸뿐이고,
    그것은 다른 보호자의 화면에 안 보입니다.
    """
    pet = await pet_repo.get_owned(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError

    pet.name = name
    await session.commit()
    return pet


# ── 프로필 사진 (D-052) ──────────────────────────────────────────────────
#
# 흐름은 보행·점령지와 같습니다: **티켓 발급 → 앱이 저장소에 직접 PUT → confirm**.
# 저장소를 GCS 로 되돌려도 앱 코드가 안 바뀌게 하려고 같은 모양을 씁니다.
#
# 사진은 한 아이에 **한 장**이고 교체됩니다. 그래서 새 키로 올리고 confirm 에서
# 옛 객체를 지웁니다 — 같은 키에 덮어쓰면 앱이 옛 사진을 계속 그립니다.


def _photo_object_keys(pet: Pet) -> tuple[str, ...]:
    """이 아이가 저장소에 갖고 있는 것 전부. 확정본과 올리다 만 것 둘 다입니다."""
    keys = (pet.photo_storage_key, pet.photo_pending_key)
    return tuple(dict.fromkeys(k for k in keys if k))


async def _delete_photo_objects(pet: Pet) -> None:
    storage = get_storage()
    for key in _photo_object_keys(pet):
        storage.delete(key)


async def cleanup_photos_for_pets(session: AsyncSession, pets: list[Pet]) -> None:
    """pet 을 지우기 **전에** 사진 객체를 먼저 지웁니다.

    ⚠️ **DB 행이 먼저 사라지면 키를 잃어 객체가 영구 고아가 됩니다.** 저장소에는 FK 가
       없어서 아무도 안 치웁니다. 보행이 `cleanup_for_pets` 로 같은 순서를 지키는
       것과 같은 이유입니다.

    저장소가 꺼져 있으면(`GAIT_STORAGE=none`) 여기서 예외가 납니다. 그때는 **pet 삭제도
    막습니다** — 사진만 남기고 행을 지우면 파기가 반쪽이 되고, 그건 공개한 처리방침
    4항과 어긋납니다. 지울 사진이 아예 없으면 저장소를 건드리지 않으므로,
    사진을 한 번도 안 쓴 계정은 저장소가 꺼져 있어도 탈퇴됩니다.
    """
    for pet in pets:
        if _photo_object_keys(pet):
            await _delete_photo_objects(pet)


async def issue_photo_ticket(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    *,
    content_type: str,
) -> UploadTicket:
    """사진을 올릴 자리를 발급합니다.

    **다시 부르면 앞의 티켓은 버려집니다.** 앱이 사진을 골랐다가 다시 고르는 것이
    자연스러운 일이라 막지 않습니다. 다만 버릴 때 **올라가다 만 객체를 같이 지웁니다** —
    안 지우면 아무도 안 보는 파일이 볼륨에 쌓입니다.
    """
    if content_type not in PET_PHOTO_CONTENT_TYPES:
        raise PetPhotoConflictError(
            "unsupported_content_type", "사진은 JPEG 또는 WebP 여야 합니다."
        )

    pet = await pet_repo.get_owned(session, app_user_id, pet_id, for_update=True)
    if pet is None:
        raise PetNotFoundError

    storage = get_storage()
    stale_key = pet.photo_pending_key

    object_key = build_pet_photo_key(pet.id, content_type=content_type)
    ticket = storage.create_upload_ticket(
        object_key=object_key,
        content_type=content_type,
        bridge_upload_path=PET_PHOTO_BRIDGE_UPLOAD_PATH,
        # 같은 티켓으로 두 번 못 올립니다. 키를 흘려도 살아 있는 객체를 못 덮습니다.
        create_only=True,
    )

    pet.photo_pending_key = object_key
    pet.photo_pending_content_type = content_type
    pet.photo_pending_at = datetime.now(UTC)
    await session.commit()

    # **commit 뒤에 지웁니다.** 먼저 지우고 commit 이 실패하면 DB 는 그 키를 계속
    # 가리키는데 객체는 없는 상태가 됩니다. 반대 순서면 최악이 고아 파일 하나입니다.
    if stale_key:
        try:
            storage.delete(stale_key)
        except Exception:  # noqa: BLE001 — 티켓 발급까지 실패시킬 이유가 없습니다
            log.warning("버린 프로필 사진 티켓 정리 실패 key=%s", stale_key)

    return ticket


async def confirm_photo(session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID) -> Pet:
    """올라온 사진을 확정합니다. **여기서 처음으로 화면에 보입니다.**

    저장소를 직접 보고 크기·형식을 확인합니다 — 앱이 "올렸어요" 라고 말하는 것만
    믿으면 없는 사진을 가리키는 행이 생깁니다.
    """
    pet = await pet_repo.get_owned(session, app_user_id, pet_id, for_update=True)
    if pet is None:
        raise PetNotFoundError
    if pet.photo_pending_key is None:
        raise PetPhotoConflictError("no_pending_photo", "먼저 사진 올릴 자리를 받으세요.")

    storage = get_storage()
    stored = storage.stat(pet.photo_pending_key)
    if stored is None:
        raise PetPhotoConflictError("photo_not_uploaded", "업로드된 사진을 찾을 수 없습니다.")
    if stored.size_bytes <= 0 or stored.size_bytes > MAX_PET_PHOTO_BYTES:
        raise PetPhotoConflictError(
            "invalid_photo_size",
            f"사진은 비어 있지 않은 {MAX_PET_PHOTO_BYTES // (1024 * 1024)} MiB 이하 파일이어야 합니다.",
        )
    # 로컬 저장소는 content_type 을 안 돌려줍니다(None). 그때는 bridge 라우터가
    # PUT 헤더를 이미 대조했으므로 여기서 또 볼 것이 없습니다.
    if stored.content_type is not None and stored.content_type != pet.photo_pending_content_type:
        raise PetPhotoConflictError(
            "photo_content_type_mismatch",
            "업로드된 파일의 Content-Type 이 발급된 형식과 다릅니다.",
        )

    previous_key = pet.photo_storage_key

    pet.photo_storage_key = pet.photo_pending_key
    pet.photo_content_type = pet.photo_pending_content_type
    pet.photo_generation = stored.generation
    pet.photo_size_bytes = stored.size_bytes
    pet.photo_updated_at = datetime.now(UTC)
    pet.photo_pending_key = None
    pet.photo_pending_content_type = None
    pet.photo_pending_at = None
    await session.commit()

    # 옛 사진은 **바뀐 뒤에** 지웁니다. 먼저 지우면 commit 실패 때 화면이 없는
    # 파일을 가리킵니다.
    if previous_key:
        try:
            storage.delete(previous_key)
        except Exception:  # noqa: BLE001
            log.warning("바꾸기 전 프로필 사진 정리 실패 key=%s", previous_key)

    return pet


async def photo_download_url(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> tuple[Pet, str]:
    """사진을 내려받을 주소. 사진이 없으면 404 입니다.

    **여기만 구성원 기준입니다** (docs/co-care.md §2) — 돌보미 화면에도 그 아이의 얼굴이
    떠야 합니다. 사진을 **올리고 지우는** 쪽(`issue_photo_ticket`·`confirm_photo`·
    `delete_photo`)은 `get_owned` 그대로입니다: 되돌릴 수 없는 것은 대표만 합니다.
    """
    from daengs_backend.config import settings

    pet = await pet_repo.get_accessible(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError
    if pet.photo_storage_key is None:
        raise PetPhotoConflictError("no_photo", "이 아이는 아직 사진이 없어요.")

    url = get_storage().download_url(
        pet.photo_storage_key,
        expires_in_seconds=settings.gait_download_url_ttl_seconds,
        generation=pet.photo_generation,
        bridge_download_path=PET_PHOTO_BRIDGE_DOWNLOAD_PATH,
    )
    return pet, url


async def delete_photo(session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID) -> None:
    """사진을 지웁니다. **앱은 다시 견종 그림으로 돌아갑니다.**

    올리다 만 티켓도 같이 걷어냅니다 — "지웠는데 잠시 뒤에 다시 나타나는" 것을
    막습니다.
    """
    pet = await pet_repo.get_owned(session, app_user_id, pet_id, for_update=True)
    if pet is None:
        raise PetNotFoundError

    keys = _photo_object_keys(pet)
    if not keys:
        return

    # **객체를 먼저 지웁니다.** 행을 먼저 비우면 키를 잃어 객체가 영구 고아입니다.
    # 여기서 실패하면 아무것도 안 바뀝니다 — 다시 부르면 됩니다.
    await _delete_photo_objects(pet)

    pet.photo_storage_key = None
    pet.photo_content_type = None
    pet.photo_generation = None
    pet.photo_size_bytes = None
    pet.photo_updated_at = None
    pet.photo_pending_key = None
    pet.photo_pending_content_type = None
    pet.photo_pending_at = None
    await session.commit()


async def delete_pet(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    *,
    confirm: bool = False,
) -> None:
    """삭제. **대표를 지우면 남은 아이 중 먼저 등록한 아이가 승계합니다.**

    "대표가 없는 상태"를 안 만들면 화면이 단순해집니다 — 앱이 매번 "대표가 없으면"
    을 다루지 않아도 됩니다. 마지막 한 마리를 지우면 그때만 대표가 없습니다.

    FK 가 `ON DELETE SET NULL` 이라 지우면 `primary_pet_id` 는 저절로 비지만,
    **누구를 대신 세울지는 정책이라 DB 가 못 정합니다.**

    ⚠️ **그 수선을 대표뿐 아니라 돌보미에게도 해 줍니다** (docs/co-care.md §3). 여기서는
    `pets` 행이 **진짜로** 지워지므로 돌보미 쪽 FK 도 이번엔 돌아서, 그 아이를
    `primary_pet_id` 로 가리키던 돌보미들이 한꺼번에 NULL 이 됩니다. 그러면 그 사람들의
    앱 첫 화면이 빕니다 — 내보내기·나가기(`services/pet_member.py::remove_member`)가 이미
    같은 일을 하므로 규칙도 그것과 같습니다: 남은 **구성원** 강아지 중 `list_for_owner`
    정렬의 첫 아이, 없으면 `None`.

    **돌보미가 남은 아이는 `confirm=True` 없이는 `PetHasCarersError` 로 막습니다**
    (Task 13, docs/co-care.md §3). 탈퇴 가드(`OwnerHasCarersError`)와 다른 메커니즘입니다 —
    그쪽은 하드 블록, 여기는 확인-후-통과입니다. 클래스 독스트링에 이유를 적었습니다.
    """
    from daengs_backend.services.activity_game import acquire

    await acquire(session)
    pet = await pet_repo.get_owned(session, app_user_id, pet_id, for_update=True)
    if pet is None:
        raise PetNotFoundError

    # ⚠️ **연결된 강아지는 그룹 주보호자만 지웁니다** (MVP 결정 §6). B 가 자기 행(202)의
    #    대표라는 것만으로 지우게 두면, 그룹이 함께 보던 기록의 한쪽이 통째로 사라집니다.
    #    `confirm` 으로도 안 뚫립니다 — 아래 `PetHasCarersError` 와 성격이 다릅니다(저쪽은
    #    "알고 하는 것이 맞나" 확인이고, 이쪽은 "당신이 할 일이 아니다" 입니다).
    identity_id = pet.identity_id
    await identity_service.require_group_owner(session, app_user_id, pet)

    # ⚠️ **소유 확인(`get_owned`) 바로 다음, 어떤 cleanup 도 하기 전**이라야 합니다.
    #    gait·사진·산책을 먼저 지운 뒤에 게이트를 걸면 409 를 받은 사용자가 다시
    #    `confirm=true` 로 불렀을 때 이미 반쯤 지워진 상태에서 재개해야 합니다.
    #    `list_members` 는 방금 읽은 **지금** 명단이라, `actor_label` 이 하는 "지금도
    #    구성원인가" 재확인이 필요 없습니다 — 그 재확인은 오래된 케어 로그의
    #    `actor_app_user_id` 처럼 "그때는 구성원이었는지 모르는" 값에만 필요합니다.
    if not confirm:
        carer_ids = await member_repo.list_members(session, pet.id)
        if carer_ids:
            names = await app_user_repo.nicknames_by_ids(session, carer_ids)
            raise PetHasCarersError(pet.name, [(cid, names.get(cid)) for cid in carer_ids])

    try:
        # gait 행이 pet FK CASCADE 로 사라지기 전에, 잠근 행에서 원본·overlay 키를
        # 읽어 모두 지웁니다. storage 실패면 아래 pet/walk 삭제로 진행하지 않습니다.
        await gait_service.cleanup_for_pets(session, [pet.id])

        # 프로필 사진도 같은 자리에서 지웁니다. **행이 먼저 사라지면 키를 잃어
        # 객체가 영구 고아입니다** — 저장소에는 FK 가 없습니다.
        await cleanup_photos_for_pets(session, [pet])

        # **그 아이와만 나간 산책은 같이 지웁니다.** 아이를 지웠는데 그 아이의 산책만
        # 주인 없이 남으면 목록에 "누구와 갔는지 모르는 기록" 이 쌓입니다.
        # 다른 아이와 같이 나간 산책은 **남깁니다** — 그건 남은 아이의 기록이기도 합니다.
        await walk_repo.delete_walks_only_with(session, pet.id)

        user = await app_user_repo.get_by_id(session, app_user_id)
        was_primary = user is not None and user.primary_pet_id == pet.id

        # 돌보미도 **지우기 전에** 모읍니다. 지운 뒤에는 `pet_members` 가 CASCADE 로
        # 사라져 누가 돌보던 아이인지 알 길이 없고, 그들의 `primary_pet_id` 는 이미
        # FK 가 NULL 로 만든 뒤라 "그 아이를 가리켰는가" 도 못 봅니다.
        carers = [
            carer
            for carer in [
                await app_user_repo.get_by_id(session, carer_id)
                for carer_id in await member_repo.list_members(session, pet.id)
            ]
            if carer is not None and carer.primary_pet_id == pet.id
        ]

        await pet_repo.delete(session, pet)
        await session.flush()

        # 그룹에 혼자 남은 행은 연결을 풀어 줍니다. 앵커(그룹 주보호자의 행)를 지운
        # 경우에는 `pet_identities.owner_pet_id` 의 CASCADE 가 그룹 행을 이미 없애고
        # `pets.identity_id` 의 SET NULL 이 남은 행을 되돌려 놓아, 여기서 할 일이
        # 없습니다 — 그래도 부르는 이유는 **그 사실에 기대지 않기 위해서**입니다.
        if identity_id is not None:
            await identity_service.prune(session, identity_id)

        if was_primary and user is not None:
            remaining = await pet_repo.list_for_owner(session, app_user_id)
            user.primary_pet_id = remaining[0].id if remaining else None

        # 돌보미는 자기가 **돌보는** 아이 중에서 고릅니다 — 대표처럼 `list_for_owner` 로
        # 고르면 남의 집 아이를 못 세워 첫 화면이 빈 채로 남습니다.
        for carer in carers:
            remaining_for_carer = await pet_repo.list_accessible(session, carer.id)
            carer.primary_pet_id = (
                remaining_for_carer[0].id if remaining_for_carer else None
            )

        await session.commit()
    except Exception:
        # object 삭제 뒤 DB commit 실패도 여기로 옵니다. DB 행·키는 rollback 으로 남고,
        # 다음 요청은 이미 없는 object 를 성공으로 보고 다시 진행합니다.
        await session.rollback()
        raise


async def delete_all_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """탈퇴용 bulk delete. 단일 삭제와 같은 cleanup 경로를 씁니다.

    보행 영상과 **프로필 사진 둘 다** 지우고 나서 행을 지웁니다 — 공개한 처리방침
    4항("탈퇴 시 지체 없이 파기")을 지키려면 DB 행만으로는 모자랍니다.

    ⚠️ **돌보미가 남은 아이가 하나라도 있으면 거절합니다 (`OwnerHasCarersError`).**
    검사는 아래 `list_for_owner_for_update` 가 `pets` 행을 잠근 **바로 다음**, 무엇을
    지우기도 전이라야 합니다 — 검사와 삭제 사이에 초대 수락이 끼어들면(둘 다 같은
    `pets` 행을 잠그므로 줄을 서지만, 검사를 락 밖에 두면 그 줄이 의미가 없어집니다)
    방금까지 돌보미가 있던 강아지가 대표 탈퇴로 조용히 지워집니다. 돌보미로만 참여
    중인 사람은 여기 걸리지 않습니다 — 이 함수는 **대표인** pets 행만 봅니다.
    """
    from daengs_backend.services.activity_game import acquire

    await acquire(session)
    pets = await pet_repo.list_for_owner_for_update(session, app_user_id)

    shared = [p.name for p in pets if await member_repo.list_members(session, p.id)]
    if shared:
        raise OwnerHasCarersError(shared)

    await gait_service.cleanup_for_pets(session, [pet.id for pet in pets])
    await cleanup_photos_for_pets(session, pets)

    # 연결돼 있던 그룹은 지우기 **전에** 적어 둡니다 — 행이 사라진 뒤에는 어느 그룹이었는지
    # 알 길이 없습니다.
    #
    # 여기 걸리는 것은 **연결된 공동 보호자**의 탈퇴뿐입니다. 그룹 주보호자는 자기 아이에
    # 돌보미가 남아 있어 위 `OwnerHasCarersError` 에서 이미 막힙니다. 공동 보호자가 나가면
    # 그룹에 앵커 하나만 남으므로 `prune` 이 연결을 풀어 원래 모양으로 되돌립니다.
    identity_ids = {p.identity_id for p in pets if p.identity_id is not None}

    deleted = await pet_repo.delete_all_for_owner(session, app_user_id)
    for identity_id in identity_ids:
        await identity_service.prune(session, identity_id)
    return deleted


async def set_primary(session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID) -> AppUser:
    """대표를 바꿉니다. **내 강아지인지 여기서 확인합니다.**

    FK 는 "존재하는 pets 행"까지만 보장하고 그게 내 것인지는 안 봅니다
    (05_pets.sql 주석). 그래서 이 검사를 빠뜨리면 남의 강아지를 내 대표로 세울 수
    있습니다.
    """
    pet = await pet_repo.get_owned(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError

    user = await app_user_repo.get_by_id(session, app_user_id)
    if user is None:
        raise PetNotFoundError
    user.primary_pet_id = pet.id
    await session.commit()
    return user
