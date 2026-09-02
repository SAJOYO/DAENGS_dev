"""`/app/gait/*` — 보행 분석 orchestration API (D-043).

기존 `/gait/*`(gait-analysis FastAPI 직접 호출)의 **proxy 가 아닙니다.** backend 가
소유하는 새 계약입니다 — 인증 · pet 소유권 · record/job lifecycle · presigned 발급.
영상 바이너리는 여기를 지나가지 않고, 분석은 별도 워커에서 돕니다.

기존 `/gait/*` 는 앱(#64)이 이쪽으로 전환한 뒤 단계적으로 제거합니다.

⚠️ **없는 것과 남의 것은 같은 404 입니다** — 403 을 주면 "그 기록이 존재한다"가
   샙니다 (pet 라우터와 같은 규칙).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.core.storage import StorageNotConfiguredError
from daengs_backend.models.gait_record import GaitRecord
from daengs_backend.repositories import gait_record as gait_repo
from daengs_backend.schemas.gait import (
    GaitAnalyzeRequest,
    GaitDeleteResponse,
    GaitRecordDetail,
    GaitRecordListResponse,
    GaitRecordSummary,
    GaitUploadTicketResponse,
)
from daengs_backend.services import gait as gait_service

router = APIRouter(prefix="/app/gait", tags=["gait"])

Session = Annotated[AsyncSession, Depends(get_session)]

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "기록을 찾을 수 없습니다.")


def _summary(r: GaitRecord) -> dict:
    return {
        "record_id": r.id,
        "pet_id": r.pet_id,
        "status": r.status,
        "quality_status": r.quality_status,
        "quality_tier": r.quality_tier,
        "gait_filter_version": r.gait_filter_version,
        "captured_at": r.captured_at,
        "source_file": r.source_file,
        "note": r.note,
        "created_at": r.created_at,
        # /v1 시절 계약의 파생 필드 — 비교 화면이 고를 수 있는 것만 보여주는 데 씁니다.
        "comparable": r.status == "DONE" and r.quality_status == "ok",
        "has_overlay": r.overlay_storage_key is not None,
    }


@router.post("/analyze", response_model=GaitUploadTicketResponse, status_code=201)
async def start_analysis(
    user: CurrentAppUser, session: Session, req: GaitAnalyzeRequest
) -> GaitUploadTicketResponse:
    """기록 생성(PENDING) + 앱이 스토리지에 직접 올릴 티켓.

    영상은 **backend 를 거치지 않습니다** — 티켓의 `upload_url` 로 직접 올리고
    `confirm` 을 부르세요.
    """
    try:
        record, ticket = await gait_service.start_analysis(
            session, user.app_user_id, req
        )
    except gait_service.NotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "강아지를 찾을 수 없습니다.") from None
    except StorageNotConfiguredError as exc:
        # 요청이 틀린 게 아니라 환경이 덜 갖춰진 것 — /ask 의 503 규칙과 같습니다.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    return GaitUploadTicketResponse(
        record_id=record.id,
        status=record.status,
        upload_url=ticket.upload_url,
        upload_headers=ticket.headers,
        expires_in_seconds=ticket.expires_in_seconds,
    )


@router.post("/records/{record_id}/confirm", response_model=GaitRecordSummary)
async def confirm_upload(
    user: CurrentAppUser, session: Session, record_id: uuid.UUID
) -> GaitRecordSummary:
    """업로드 완료 신고 → 실존 확인 → 분석 큐 발행."""
    try:
        record = await gait_service.confirm_upload(session, user.app_user_id, record_id)
    except gait_service.NotFoundError:
        raise _NOT_FOUND from None
    except gait_service.WrongStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except StorageNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    return GaitRecordSummary(**_summary(record))


@router.get("/records", response_model=GaitRecordListResponse)
async def list_records(
    user: CurrentAppUser,
    session: Session,
    pet_id: uuid.UUID,
    limit: int = 20,
    cursor: uuid.UUID | None = None,
) -> GaitRecordListResponse:
    """한 강아지의 기록, 오래된 것부터. 요약만 — 무거운 값은 단건 조회로."""
    if limit < 1 or limit > 100:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "limit 은 1~100 입니다.")
    try:
        rows = await gait_repo.list_for_pet(
            session, user.app_user_id, pet_id, limit=limit, cursor=cursor
        )
    except LookupError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "cursor 가 유효하지 않습니다 (지워진 기록일 수 있습니다).",
        ) from None
    has_more = len(rows) > limit
    page = rows[:limit]
    return GaitRecordListResponse(
        records=[GaitRecordSummary(**_summary(r)) for r in page],
        next_cursor=page[-1].id if (page and has_more) else None,
    )


@router.get("/records/{record_id}", response_model=GaitRecordDetail)
async def get_record(
    user: CurrentAppUser, session: Session, record_id: uuid.UUID
) -> GaitRecordDetail:
    record = await gait_repo.get_owned(session, user.app_user_id, record_id)
    if record is None:
        raise _NOT_FOUND
    return GaitRecordDetail(
        **_summary(record),
        quality=record.quality,
        summary_for_ui=record.summary_for_ui,
        video_meta=record.video_meta,
        failure_reason=record.failure_reason,
    )


@router.delete("/records/{record_id}", response_model=GaitDeleteResponse)
async def delete_record(
    user: CurrentAppUser, session: Session, record_id: uuid.UUID
) -> GaitDeleteResponse:
    """원본·overlay 정리가 성공한 뒤 기록을 지웁니다."""
    try:
        record = await gait_service.soft_delete(session, user.app_user_id, record_id)
    except gait_service.NotFoundError:
        raise _NOT_FOUND from None
    return GaitDeleteResponse(record_id=record.id, deleted=True)


# ── 임시 bridge (gait_storage="local" 전용) ─────────────────────────────
#
# ⚠️ **프로덕션 경로가 아닙니다.** GCS(원칙 1)는 앱이 스토리지에 직접 올려 영상이
#    backend 를 통과하지 않습니다. 이 두 엔드포인트는 GCS 자격증명 없이 `/app/gait/*`
#    왕복을 검증하려는 검증용이고, `gait_storage="gcs"` 로 바꾸면 등록만 되어 있고
#    아무 키도 받지 못합니다(아래 `_local_bridge` 가 404).
#
# ⚠️ **인증 헤더를 요구하지 않습니다 — 대신 키가 자격입니다.** Signed URL 을 흉내 내는
#    자리라, 헤더를 요구하면 GCS 로 바꿀 때 앱 코드가 또 바뀝니다. 그래서 대신
#    **backend 가 실제로 발급한 키인지**를 DB 로 확인합니다 (find_by_storage_key).
#    이게 없으면 아무나 임의 경로로 서버 디스크를 채울 수 있습니다 — 실제로 그 상태로
#    한 번 배포됐고(2026-09-02), 이 검사가 그것을 막습니다.


def _local_bridge():
    from daengs_backend.core.storage import LocalBridgeStorage, get_storage

    storage = get_storage()
    if not isinstance(storage, LocalBridgeStorage):
        # gcs/none 모드에서는 이 경로가 없는 것처럼 404.
        raise _NOT_FOUND
    return storage


@router.put("/_bridge/upload/{storage_key:path}", include_in_schema=False)
async def _bridge_upload(session: Session, storage_key: str, request: Request):
    from fastapi import Response

    storage = _local_bridge()
    # 발급된 적 없는 키 · 이미 confirm 된 키 = 없는 경로와 같은 404.
    # (PENDING 으로 좁히므로 confirm 뒤 덮어쓰기도 막힙니다.)
    if await gait_repo.find_by_storage_key(session, storage_key, status="PENDING") is None:
        raise _NOT_FOUND
    storage.write(storage_key, await request.body())
    return Response(status_code=200)


@router.get("/_bridge/download/{storage_key:path}", include_in_schema=False)
async def _bridge_download(session: Session, storage_key: str):
    from fastapi.responses import FileResponse

    storage = _local_bridge()
    # ⚠️ **overlay 도 받을 수 있어야 합니다** — 분석 결과 영상이 그것입니다.
    #    업로드(위)는 원본 키만 받습니다: overlay 를 앱이 덮어쓰면 안 됩니다.
    if await gait_repo.find_by_storage_key(session, storage_key, allow_overlay=True) is None:
        raise _NOT_FOUND
    path = storage.local_path(storage_key)
    if not path.exists():
        raise _NOT_FOUND
    return FileResponse(path)
