"""`/app/screening/*` — 피부 변화 기록 API (D-052).

**옛 `/screen/v1/screen` 의 proxy 가 아닙니다.** backend 가 소유하는 새 계약입니다 —
인증 · 아이 소유권 · 기록 원장 · 사진 저장.

⚠️ **옛 경로는 그대로 둡니다.** 지금 앱이 그대로 쓰고 있어서, 거기에 인증을 붙이면
   앱이 새로 나가기 전까지 **디버그 빌드에서 피부가 401 로 죽습니다.** 보행이
   `/gait/*` → `/app/gait/*` 로 옮길 때와 같은 방식으로, 앱이 옮겨간 뒤 옛 경로를
   410 으로 닫는 것은 별도 카드입니다 (D-043 선례. 404 가 아니라 410 인 이유는
   "없는 척하면 옛 앱이 서버가 잠깐 이상한가 하고 재시도" 하기 때문입니다).

⚠️ **없는 것과 남의 것은 같은 404 입니다** — 403 을 주면 "그 기록이 존재한다" 가
   샙니다 (pet · gait 라우터와 같은 규칙).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.core.storage import StorageNotConfiguredError
from daengs_backend.models import ScreeningRecord
from daengs_backend.repositories import screening as screening_repo
from daengs_backend.schemas.screening import (
    ScreeningListResponse,
    ScreeningRecordResponse,
    ScreeningStartRequest,
    ScreeningTicketResponse,
)
from daengs_backend.services import screening as screening_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/app/screening", tags=["screening"])

Session = Annotated[AsyncSession, Depends(get_session)]

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "기록을 찾을 수 없습니다.")

#: 저장소가 안 켜졌을 때 **사용자에게** 보여 줄 문장.
#:
#: ⚠️ **예외 메시지를 그대로 내보내면 안 됩니다.** `StorageNotConfiguredError` 는
#:    운영자용이라 환경 변수 이름과 설정값이 들어 있습니다. 보행 라우터가 그것을
#:    앱 화면에 그대로 띄운 적이 있습니다 (2026-09-03).
_STORAGE_NOT_READY = "피부 기록은 아직 준비 중이에요."

#: 가중치가 없을 때. 저장소와 **다른 사유**라 문장을 나눠 둡니다 — 같은 말을 쓰면
#: 어느 쪽을 고쳐야 하는지 로그 없이는 알 수 없습니다.
_MODEL_NOT_READY = "피부 판정을 지금은 할 수 없어요. 잠시 뒤에 다시 시도해 주세요."


def _storage_unavailable(exc: StorageNotConfiguredError) -> HTTPException:
    log.warning("피부 기록 저장소가 준비되지 않았습니다: %s", exc)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _STORAGE_NOT_READY)


def _model_unavailable(exc: Exception) -> HTTPException:
    """가중치 사유도 **로그에만** 남깁니다 — 경로가 들어 있습니다."""
    log.warning("스크리닝 모델을 불러오지 못했습니다: %s", exc)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _MODEL_NOT_READY)


def _conflict(exc: screening_service.ScreeningConflictError) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT, {"code": exc.code, "message": exc.detail}
    )


def _to_response(
    record: ScreeningRecord, photo_url: str | None = None, perms: dict | None = None
) -> ScreeningRecordResponse:
    """`perms` 는 `screening_service.annotate` 가 준 한 건 — 안 주면(비워 둘 수 없는
    필수 필드라) 전부 False/None 입니다."""
    perms = perms or {}
    return ScreeningRecordResponse(
        record_id=record.id,
        pet_id=record.pet_id,
        status=record.status,
        created_at=record.created_at,
        result=record.result,
        contract_version=record.contract_version,
        photo_url=photo_url,
        can_confirm=perms.get("can_confirm", False),
        can_delete=perms.get("can_delete", False),
        created_by=perms.get("created_by"),
    )


@router.post(
    "/records",
    response_model=ScreeningTicketResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_record(
    body: ScreeningStartRequest,
    user: CurrentAppUser,
    session: Session,
) -> ScreeningTicketResponse:
    """기록 한 줄을 열고 사진 올릴 자리를 받습니다."""
    try:
        record, ticket = await screening_service.start_record(session, user.app_user_id, body)
    except screening_service.ScreeningNotFoundError:
        # 남의 아이를 지목했습니다. 아이 쪽 404 와 같은 말로 뭉갭니다.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다."
        ) from None
    except StorageNotConfiguredError as exc:
        raise _storage_unavailable(exc) from None
    return ScreeningTicketResponse(
        record_id=record.id,
        storage_key=ticket.storage_key,
        upload_url=ticket.upload_url,
        upload_headers=ticket.headers,
        expires_in_seconds=ticket.expires_in_seconds,
    )


@router.post("/records/{record_id}/confirm", response_model=ScreeningRecordResponse)
async def confirm_record(
    record_id: uuid.UUID,
    user: CurrentAppUser,
    session: Session,
) -> ScreeningRecordResponse:
    """사진을 확인하고 **판정까지 끝냅니다.**

    사진 한 장에 CPU 0.6~3초라 응답이 그만큼 걸립니다. 앱이 이미 옛 경로에서 같은
    시간을 기다리고 있어서 새로 생기는 대기가 아닙니다.
    """
    try:
        record = await screening_service.confirm_record(session, user.app_user_id, record_id)
    except screening_service.ScreeningNotFoundError:
        raise _NOT_FOUND from None
    except screening_service.ScreeningModelUnavailableError as exc:
        raise _model_unavailable(exc) from None
    except screening_service.ScreeningConflictError as exc:
        raise _conflict(exc) from None
    except StorageNotConfiguredError as exc:
        raise _storage_unavailable(exc) from None
    perms = (
        await screening_service.annotate(session, user.app_user_id, [record])
    ).get(record.id)
    return _to_response(record, perms=perms)


@router.get("/records", response_model=ScreeningListResponse)
async def list_records(
    user: CurrentAppUser,
    session: Session,
    pet_id: Annotated[uuid.UUID | None, Query()] = None,
) -> ScreeningListResponse:
    """내 기록을 **최근 순**으로.

    **사진 주소를 안 싣습니다** — N 개마다 저장소를 두드리게 되고, 앱이 안 그리는
    것까지 만듭니다. 목록에서 하나를 고르면 단건 조회가 주소를 줍니다.
    """
    try:
        records = await screening_service.list_records(session, user.app_user_id, pet_id=pet_id)
    except screening_service.ScreeningNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다."
        ) from None
    # 한 번에 계산합니다 — 행마다 부르면 페이지 크기만큼 왕복합니다
    # (screening_service.annotate 독스트링, Task 19).
    perms_by_id = await screening_service.annotate(session, user.app_user_id, records)
    return ScreeningListResponse(
        records=[_to_response(r, perms=perms_by_id.get(r.id)) for r in records]
    )


@router.get("/records/{record_id}", response_model=ScreeningRecordResponse)
async def get_record(
    record_id: uuid.UUID,
    user: CurrentAppUser,
    session: Session,
) -> ScreeningRecordResponse:
    """기록 하나와 사진 주소."""
    try:
        record, url = await screening_service.get_record(session, user.app_user_id, record_id)
    except screening_service.ScreeningNotFoundError:
        raise _NOT_FOUND from None
    except StorageNotConfiguredError as exc:
        raise _storage_unavailable(exc) from None
    perms = (
        await screening_service.annotate(session, user.app_user_id, [record])
    ).get(record.id)
    return _to_response(record, url, perms=perms)


@router.delete("/records/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(
    record_id: uuid.UUID,
    user: CurrentAppUser,
    session: Session,
) -> None:
    """기록 하나를 지웁니다. **사진 파일까지 지웁니다.**"""
    try:
        await screening_service.delete_record(session, user.app_user_id, record_id)
    except screening_service.ScreeningNotFoundError:
        raise _NOT_FOUND from None
    except StorageNotConfiguredError as exc:
        raise _storage_unavailable(exc) from None


# ── local 저장소의 bridge ────────────────────────────────────────────────
#
# ⚠️ **인증 헤더를 요구하지 않습니다 — 대신 키가 자격입니다.** Signed URL 을 흉내 내는
#    자리라, 헤더를 요구하면 저장소를 GCS 로 되돌릴 때 앱 코드가 또 바뀝니다. 대신
#    **backend 가 실제로 발급한 키인지**를 DB 로 확인합니다. 키에 record_id(uuid)가
#    들어 있어 추측이 안 되는 것이 나머지 절반입니다.


def _local_bridge():
    from daengs_backend.core.storage import LocalBridgeStorage, get_storage

    storage = get_storage()
    if not isinstance(storage, LocalBridgeStorage):
        # gcs/none 모드에서는 이 경로가 없는 것처럼 404.
        raise _NOT_FOUND
    return storage


@router.put("/_bridge/upload/{storage_key:path}", include_in_schema=False)
async def _bridge_upload(session: Session, storage_key: str, request: Request) -> Response:
    """발급된 `PENDING_UPLOAD` 키 하나에만 사진을 받습니다. 디스크로 흘려 씁니다.

    **판정이 끝난 기록에는 못 올립니다** — PENDING_UPLOAD 로 좁히므로 confirm 뒤
    덮어쓰기가 막힙니다. 그러면 화면의 판정과 사진이 어긋납니다.
    """
    storage = _local_bridge()
    record = await screening_repo.find_by_storage_key(
        session, storage_key, status="PENDING_UPLOAD"
    )
    if record is None:
        raise _NOT_FOUND

    declared_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if declared_type != record.photo_content_type:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "발급된 사진 형식과 Content-Type 이 다릅니다.",
        )

    limit = screening_service.MAX_SCREENING_PHOTO_BYTES
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

    # exclusive=True 는 create-only 입니다. 도중에 끊기면 open_write 가 반쯤 쓴
    # 파일을 지웁니다 — 남으면 다음 PUT 이 409 에 막히고, 0바이트로 남으면
    # redact() 의 tombstone 과 구별되지 않습니다.
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
    """기록의 사진을 내려줍니다.

    **상태로 좁히지 않습니다** — `FAILED` 기록의 사진도 화면에 보여야 합니다.
    판정이 안 됐을 뿐 사용자가 찍은 사진이고, 다시 찍을지 정하려면 봐야 합니다.
    """
    from fastapi.responses import FileResponse

    storage = _local_bridge()
    record = await screening_repo.find_by_storage_key(session, storage_key)
    if record is None:
        raise _NOT_FOUND
    path = storage.local_path(storage_key)
    if not path.exists():
        raise _NOT_FOUND
    return FileResponse(path, media_type=record.photo_content_type)
