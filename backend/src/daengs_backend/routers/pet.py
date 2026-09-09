"""강아지 프로필 HTTP 경계 — 서비스의 예외를 상태 코드로 바꿉니다.

판단은 여기 없습니다. "몇 마리까지"·"대표를 누구로"는 services/pet.py 가 정합니다.

**경로가 `/app/pets` 인 이유**는 앱 회원 전용이기 때문입니다. 관리자 콘솔이
나중에 회원의 강아지를 볼 일이 생기면 `/admin/...` 아래에 따로 둡니다 —
같은 경로가 주체에 따라 다르게 동작하면 한 글자 차이로 엉뚱한 쪽을 부릅니다
(`/auth/app/*` 을 나눈 것과 같은 이유).
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.core.storage import StorageNotConfiguredError
from daengs_backend.models import Pet
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.schemas.pet import (
    PetListResponse,
    PetPhotoResponse,
    PetPhotoTicketRequest,
    PetPhotoTicketResponse,
    PetResponse,
    PetUpsert,
    PrimaryPetRequest,
)
from daengs_backend.services import pet as pet_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/app/pets", tags=["pets"])

Session = Annotated[AsyncSession, Depends(get_session)]

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다.")

#: 저장소가 안 켜졌을 때 **사용자에게** 보여 줄 문장.
#:
#: ⚠️ **예외 메시지를 그대로 내보내면 안 됩니다.** `StorageNotConfiguredError` 는
#:    운영자용이라 환경 변수 이름과 설정값이 들어 있습니다. 보행 라우터가 그것을
#:    앱 화면에 그대로 띄운 적이 있어(2026-09-03) 같은 방식으로 막습니다.
_PHOTO_NOT_READY = "사진 기능은 아직 준비 중이에요."


def _photo_unavailable(exc: StorageNotConfiguredError) -> HTTPException:
    """503 으로 바꾸면서 **사유는 로그에만** 남깁니다."""
    log.warning("프로필 사진 저장소가 준비되지 않았습니다: %s", exc)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _PHOTO_NOT_READY)


def _photo_conflict(exc: pet_service.PetPhotoConflictError) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT, {"code": exc.code, "message": exc.detail}
    )


def _to_response(
    pet: Pet, primary_pet_id: uuid.UUID | None, viewer: uuid.UUID
) -> PetResponse:
    """`viewer` 는 **부른 사람**입니다 — 목록에 돌보미로 참여 중인 아이가 섞여 오므로
    (docs/co-care.md §2), 그 아이의 대표가 나인지를 여기서 붙입니다."""
    return PetResponse(
        id=pet.id,
        name=pet.name,
        breed=pet.breed,
        sex=pet.sex,
        neutered=pet.neutered,
        weight_kg=pet.weight_kg,
        birth_date=pet.birth_date,
        birth_date_kind=pet.birth_date_kind,
        farewell_on=pet.farewell_on,
        feeding_style=pet.feeding_style,
        feeding_times=pet.feeding_times,
        health_conditions=pet.health_conditions,
        medications=pet.medications,
        is_primary=pet.id == primary_pet_id,
        is_owner=pet.app_user_id == viewer,
        updated_at=pet.updated_at,
        has_photo=pet.photo_storage_key is not None,
        photo_updated_at=pet.photo_updated_at,
    )


@router.get("", response_model=PetListResponse)
async def list_pets(
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PetListResponse:
    """**내가 돌보는 아이 전부.** 등록 순서대로입니다.

    돌보미로 참여 중인 아이도 섞여 옵니다 (docs/co-care.md §2). 그 아이는 `is_owner` 가
    false 라, 앱은 수정·배웅·삭제·사진 버튼을 가려야 합니다.

    `max_pets` 를 같이 보내는 이유는 앱이 `+` 버튼을 언제 감출지 정하기 때문입니다.
    앱에 숫자를 박아 두면 서버가 상한을 바꿀 때 갈라집니다.
    """
    pets, primary_id = await pet_service.list_pets(session, user.app_user_id)
    return PetListResponse(
        pets=[_to_response(p, primary_id, user.app_user_id) for p in pets],
        max_pets=pet_service.MAX_PETS_PER_USER,
    )


@router.post("", response_model=PetResponse, status_code=status.HTTP_201_CREATED)
async def create_pet(
    body: PetUpsert,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PetResponse:
    """등록합니다. 첫 아이는 자동으로 대표가 됩니다."""
    try:
        pet, primary_id = await pet_service.create_pet(session, user.app_user_id, body)
    except pet_service.PetLimitReachedError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"강아지는 {pet_service.MAX_PETS_PER_USER}마리까지 등록할 수 있습니다.",
        ) from None
    return _to_response(pet, primary_id, user.app_user_id)


# ⚠️ **`/{pet_id}` 보다 먼저 선언해야 한다.** FastAPI 는 등록 순서대로 매칭하므로,
# 뒤에 두면 `/app/pets/primary` 가 `update_pet` 으로 가서 "primary" 를 UUID 로
# 파싱하려다 422 가 난다. 실제로 그렇게 만들었다가 라우트 매칭을 재서 찾았다.
@router.put("/primary", status_code=status.HTTP_204_NO_CONTENT)
async def set_primary(
    body: PrimaryPetRequest,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """대표를 바꿉니다. 내 강아지가 아니면 404 입니다."""
    try:
        await pet_service.set_primary(session, user.app_user_id, body.pet_id)
    except pet_service.PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다.") from None


@router.put("/{pet_id}", response_model=PetResponse)
async def update_pet(
    pet_id: uuid.UUID,
    body: PetUpsert,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PetResponse:
    """전체를 다시 보냅니다(PUT).

    부분 수정을 안 두는 이유는 `None` 의 뜻이 갈리기 때문입니다 (schemas/pet.py).
    """
    try:
        pet = await pet_service.update_pet(session, user.app_user_id, pet_id, body)
    except pet_service.PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다.") from None
    _, primary_id = await pet_service.list_pets(session, user.app_user_id)
    return _to_response(pet, primary_id, user.app_user_id)


@router.delete("/{pet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pet(
    pet_id: uuid.UUID,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """지웁니다. 대표를 지우면 남은 아이 중 먼저 등록한 아이가 승계합니다."""
    try:
        await pet_service.delete_pet(session, user.app_user_id, pet_id)
    except pet_service.PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다.") from None


# ── 프로필 사진 (D-052) ──────────────────────────────────────────────────
#
# 흐름은 보행·점령지와 같습니다: **티켓 → 앱이 저장소에 직접 PUT → confirm**.
# 저장소를 GCS 로 되돌려도 앱이 안 바뀌게 하려고 같은 모양을 씁니다.
#
# ⚠️ `/{pet_id}/photo` 는 두 조각이라 `/{pet_id}`(한 조각)와 안 부딪힙니다.
#    `/primary` 때처럼 순서를 신경 쓸 자리가 아닙니다.


@router.post(
    "/{pet_id}/photo",
    response_model=PetPhotoTicketResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_photo_ticket(
    pet_id: uuid.UUID,
    user: CurrentAppUser,
    session: Session,
    body: PetPhotoTicketRequest | None = None,
) -> PetPhotoTicketResponse:
    """사진 올릴 자리를 받습니다. **다시 부르면 앞의 티켓은 버려집니다.**

    본문을 안 보내면 JPEG 로 봅니다 — 앱이 카메라에서 받아 압축하는 기본 형식입니다.
    """
    requested = body or PetPhotoTicketRequest()
    try:
        ticket = await pet_service.issue_photo_ticket(
            session, user.app_user_id, pet_id, content_type=requested.content_type
        )
    except pet_service.PetNotFoundError:
        raise _NOT_FOUND from None
    except pet_service.PetPhotoConflictError as exc:
        raise _photo_conflict(exc) from None
    except StorageNotConfiguredError as exc:
        raise _photo_unavailable(exc) from None
    return PetPhotoTicketResponse(
        storage_key=ticket.storage_key,
        upload_url=ticket.upload_url,
        upload_headers=ticket.headers,
        expires_in_seconds=ticket.expires_in_seconds,
    )


@router.post("/{pet_id}/photo/confirm", response_model=PetResponse)
async def confirm_photo(
    pet_id: uuid.UUID,
    user: CurrentAppUser,
    session: Session,
) -> PetResponse:
    """올라온 사진을 확정합니다. **여기서 처음으로 화면에 보입니다.**"""
    try:
        pet = await pet_service.confirm_photo(session, user.app_user_id, pet_id)
    except pet_service.PetNotFoundError:
        raise _NOT_FOUND from None
    except pet_service.PetPhotoConflictError as exc:
        raise _photo_conflict(exc) from None
    except StorageNotConfiguredError as exc:
        raise _photo_unavailable(exc) from None
    _, primary_id = await pet_service.list_pets(session, user.app_user_id)
    return _to_response(pet, primary_id, user.app_user_id)


@router.get("/{pet_id}/photo", response_model=PetPhotoResponse)
async def get_photo(
    pet_id: uuid.UUID,
    user: CurrentAppUser,
    session: Session,
) -> PetPhotoResponse:
    """사진을 내려받을 주소.

    **목록에 주소를 안 싣고 여기서 따로 주는 이유**는, 목록 한 번에 N 개의 주소를
    만들면 저장소를 N 번 두드리고 앱이 안 그리는 아이 것까지 만들기 때문입니다.
    """
    try:
        pet, url = await pet_service.photo_download_url(session, user.app_user_id, pet_id)
    except pet_service.PetNotFoundError:
        raise _NOT_FOUND from None
    except pet_service.PetPhotoConflictError as exc:
        raise _photo_conflict(exc) from None
    except StorageNotConfiguredError as exc:
        raise _photo_unavailable(exc) from None
    return PetPhotoResponse(
        download_url=url,
        content_type=pet.photo_content_type or "image/jpeg",
        size_bytes=pet.photo_size_bytes or 0,
        updated_at=pet.photo_updated_at,
    )


@router.delete("/{pet_id}/photo", status_code=status.HTTP_204_NO_CONTENT)
async def delete_photo(
    pet_id: uuid.UUID,
    user: CurrentAppUser,
    session: Session,
) -> None:
    """사진을 지웁니다. 앱은 다시 견종 그림으로 돌아갑니다.

    사진이 없어도 204 입니다 — 두 번 눌러도 같은 결과여야 합니다.
    """
    try:
        await pet_service.delete_photo(session, user.app_user_id, pet_id)
    except pet_service.PetNotFoundError:
        raise _NOT_FOUND from None
    except StorageNotConfiguredError as exc:
        raise _photo_unavailable(exc) from None


# ── local 저장소의 bridge ────────────────────────────────────────────────
#
# ⚠️ **인증 헤더를 요구하지 않습니다 — 대신 키가 자격입니다.** Signed URL 을 흉내 내는
#    자리라, 헤더를 요구하면 저장소를 GCS 로 되돌릴 때 앱 코드가 또 바뀝니다. 그래서
#    대신 **backend 가 실제로 발급한 키인지**를 DB 로 확인합니다. 이게 없으면 아무나
#    임의 경로로 서버 디스크를 채울 수 있습니다 (보행 bridge 가 2026-09-02 에 실제로
#    그 상태로 배포됐습니다). 키에 uuid 가 들어 추측이 안 되는 것이 나머지 절반입니다.


def _local_bridge():
    from daengs_backend.core.storage import LocalBridgeStorage, get_storage

    storage = get_storage()
    if not isinstance(storage, LocalBridgeStorage):
        # gcs/none 모드에서는 이 경로가 없는 것처럼 404.
        raise _NOT_FOUND
    return storage


@router.put("/_bridge/upload/{storage_key:path}", include_in_schema=False)
async def _bridge_upload(
    session: Session, storage_key: str, request: Request
) -> Response:
    """발급된 **대기 키** 하나에만 사진을 받습니다. 디스크로 흘려 씁니다.

    **확정 키로는 못 올립니다** — pending 으로 좁히므로 confirm 뒤 덮어쓰기가 막힙니다.
    """
    storage = _local_bridge()
    pet = await pet_repo.find_by_photo_key(session, storage_key, pending=True)
    if pet is None:
        raise _NOT_FOUND

    declared_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if declared_type != pet.photo_pending_content_type:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "발급된 사진 형식과 Content-Type 이 다릅니다.",
        )

    limit = pet_service.MAX_PET_PHOTO_BYTES
    too_large = HTTPException(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        f"사진은 {limit // (1024 * 1024)} MiB 이하여야 합니다.",
    )

    # 큰 파일을 다 받고 나서 거절하면 대역폭과 디스크를 이미 쓴 뒤입니다.
    # Content-Length 는 앱이 주는 값이라 **믿지 않고**, 아래 누적 검사로 다시 봅니다.
    declared_size = request.headers.get("content-length")
    if declared_size is not None:
        try:
            if int(declared_size) > limit:
                raise too_large
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Content-Length 가 올바르지 않습니다."
            ) from None

    # exclusive=True 는 create-only 입니다 — 같은 티켓으로 두 번 못 올립니다.
    # 도중에 끊기면 open_write 가 반쯤 쓴 파일을 지웁니다.
    try:
        written = 0
        with storage.open_write(storage_key, exclusive=True) as stream:
            async for chunk in request.stream():
                written += len(chunk)
                if written > limit:
                    raise too_large
                stream.write(chunk)
            if written == 0:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "빈 사진은 업로드할 수 없습니다."
                )
    except FileExistsError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "이미 올린 사진은 같은 티켓으로 덮어쓸 수 없습니다.",
        ) from None
    return Response(status_code=status.HTTP_200_OK)


@router.get("/_bridge/download/{storage_key:path}", include_in_schema=False)
async def _bridge_download(session: Session, storage_key: str):
    """확정된 사진만 내려줍니다.

    **대기 키는 안 받습니다** — 아직 confirm 안 된 것은 크기·형식을 검사하지 않은
    바이트라, 그것을 화면에 그리면 confirm 이 하는 일이 무의미해집니다.
    """
    from fastapi.responses import FileResponse

    storage = _local_bridge()
    pet = await pet_repo.find_by_photo_key(session, storage_key, pending=False)
    if pet is None:
        raise _NOT_FOUND
    path = storage.local_path(storage_key)
    if not path.exists():
        raise _NOT_FOUND
    return FileResponse(path, media_type=pet.photo_content_type or "image/jpeg")
