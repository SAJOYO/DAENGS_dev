"""``/app/territory/attempts`` — 산책 중 점령지 방문 인증 API.

촬영 순간의 위치만 동기로 10m 판정하고, 사진은 저장소에 직접 올린 뒤
``VISION_PENDING``까지만 넘깁니다. VLM과 실제 점령 정책은 이 라우터 밖의 후속 단계입니다.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.core.storage import LocalBridgeStorage, StorageNotConfiguredError, get_storage
from daengs_backend.models.territory import TerritoryAttempt
from daengs_backend.repositories import territory as territory_repo
from daengs_backend.schemas.territory import (
    TerritoryAttemptResponse,
    TerritoryAttemptStart,
    TerritoryAttemptTicketResponse,
)
from daengs_backend.services import territory as territory_service
from daengs_backend.services.territory_site_lookup import (
    TerritorySiteLookup,
    TerritorySiteUnavailableError,
    get_territory_site_lookup,
)

router = APIRouter(prefix="/app/territory/attempts", tags=["territory"])

Session = Annotated[AsyncSession, Depends(get_session)]
SiteLookup = Annotated[TerritorySiteLookup, Depends(get_territory_site_lookup)]

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "방문 인증 시도를 찾을 수 없습니다.")


def _response_fields(attempt: TerritoryAttempt) -> dict:
    visit = attempt.verified_visit
    return {
        "attempt_id": attempt.id,
        "client_capture_id": attempt.client_capture_id,
        "client_session_id": attempt.client_session_id,
        "site_id": attempt.site_id,
        "captured_at": attempt.captured_at,
        "status": attempt.status,
        "distance_m": attempt.distance_m,
        "accuracy_m": attempt.accuracy_m,
        "verified_visit_id": visit.id if visit is not None else None,
        "vision_model": attempt.vision_model,
        "vision_model_version": attempt.vision_model_version,
        "decision_reason": attempt.decision_reason,
        "created_at": attempt.created_at,
        "updated_at": attempt.updated_at,
    }


def _conflict(exc: territory_service.TerritoryAttemptConflictError) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        {"code": exc.code, "message": exc.detail},
    )


@router.post("", response_model=TerritoryAttemptTicketResponse, status_code=201)
async def start_attempt(
    user: CurrentAppUser,
    session: Session,
    body: TerritoryAttemptStart,
    site_lookup: SiteLookup,
    response: Response,
) -> TerritoryAttemptTicketResponse:
    """촬영 증거를 고정하고 임시 사진 업로드 티켓을 발급합니다.

    ``client_capture_id`` 재전송은 같은 증거일 때만 기존 시도를 반환합니다. 앱은 응답을
    잃어도 새 시도를 만들지 않고 같은 키로 안전하게 재시도할 수 있습니다.
    """
    try:
        attempt, ticket, created = await territory_service.start_attempt(
            session,
            user.app_user_id,
            body,
            site_lookup,
        )
    except territory_service.TerritoryAttemptConflictError as exc:
        raise _conflict(exc) from None
    except TerritorySiteUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    except StorageNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None

    if not created:
        response.status_code = status.HTTP_200_OK
    return TerritoryAttemptTicketResponse(
        **_response_fields(attempt),
        upload_url=ticket.upload_url if ticket is not None else None,
        upload_headers=ticket.headers if ticket is not None else {},
        expires_in_seconds=ticket.expires_in_seconds if ticket is not None else None,
    )


@router.post("/{attempt_id}/confirm", response_model=TerritoryAttemptResponse)
async def confirm_upload(
    user: CurrentAppUser,
    session: Session,
    attempt_id: uuid.UUID,
) -> TerritoryAttemptResponse:
    """사진 실존을 확인하고 비동기 판정 대기 상태로 전환합니다."""
    try:
        attempt, _changed = await territory_service.confirm_upload(
            session, user.app_user_id, attempt_id
        )
    except territory_service.TerritoryAttemptNotFoundError:
        raise _NOT_FOUND from None
    except territory_service.TerritoryAttemptConflictError as exc:
        raise _conflict(exc) from None
    except StorageNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    return TerritoryAttemptResponse(**_response_fields(attempt))


@router.get("/{attempt_id}", response_model=TerritoryAttemptResponse)
async def get_attempt(
    user: CurrentAppUser,
    session: Session,
    attempt_id: uuid.UUID,
) -> TerritoryAttemptResponse:
    """앱이 비동기 판정 상태를 polling하는 가벼운 단건 조회입니다."""
    try:
        attempt = await territory_service.get_attempt(session, user.app_user_id, attempt_id)
    except territory_service.TerritoryAttemptNotFoundError:
        raise _NOT_FOUND from None
    return TerritoryAttemptResponse(**_response_fields(attempt))


# local 저장소 검증용 bridge. 운영 GCS에서는 등록되어 있어도 404로 닫힙니다.
def _local_bridge() -> LocalBridgeStorage:
    storage = get_storage()
    if not isinstance(storage, LocalBridgeStorage):
        raise _NOT_FOUND
    return storage


@router.put("/_bridge/upload/{storage_key:path}", include_in_schema=False)
async def _bridge_upload(session: Session, storage_key: str, request: Request) -> Response:
    """발급된 PENDING_UPLOAD 키 하나에만 최대 12 MiB 사진을 받습니다."""
    storage = _local_bridge()
    attempt = await territory_repo.find_pending_by_storage_key(session, storage_key)
    if attempt is None:
        raise _NOT_FOUND
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
        attempt.photo_content_type
    ):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "발급된 사진 형식과 Content-Type이 다릅니다.",
        )

    declared_size = request.headers.get("content-length")
    if declared_size is not None:
        try:
            if int(declared_size) > territory_service.MAX_TERRITORY_PHOTO_BYTES:
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "사진은 12 MiB 이하입니다."
                )
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Content-Length가 올바르지 않습니다."
            ) from None

    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > territory_service.MAX_TERRITORY_PHOTO_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "사진은 12 MiB 이하입니다."
            )
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "빈 사진은 업로드할 수 없습니다.")
    try:
        storage.write_if_absent(storage_key, bytes(data))
    except FileExistsError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "이미 업로드된 사진은 같은 티켓으로 덮어쓸 수 없습니다.",
        ) from None
    return Response(status_code=status.HTTP_200_OK)
