"""`/app/gait/*` — 보행 분석 orchestration API (D-043).

기존 `/gait/*`(gait-analysis FastAPI 직접 호출)의 **proxy 가 아닙니다.** backend 가
소유하는 새 계약입니다 — 인증 · pet 소유권 · record/job lifecycle · presigned 발급.
영상 바이너리는 여기를 지나가지 않고, 분석은 별도 워커에서 돕니다.

기존 `/gait/*` 는 앱(#64)이 이쪽으로 전환한 뒤 단계적으로 제거합니다.

⚠️ **없는 것과 남의 것은 같은 404 입니다** — 403 을 주면 "그 기록이 존재한다"가
   샙니다 (pet 라우터와 같은 규칙).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.config import settings
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.core.storage import StorageNotConfiguredError, get_storage
from daengs_backend.models.gait_record import GaitRecord
from daengs_backend.repositories import gait_record as gait_repo
from daengs_backend.schemas.gait import (
    GaitAnalyzeRequest,
    GaitCompareRequest,
    GaitCompareResponse,
    GaitDeleteResponse,
    GaitRecordDetail,
    GaitRecordListResponse,
    GaitRecordSummary,
    GaitUploadTicketResponse,
)
from daengs_backend.services import gait as gait_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/app/gait", tags=["gait"])

Session = Annotated[AsyncSession, Depends(get_session)]

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "기록을 찾을 수 없습니다.")

#: 저장소가 준비되지 않았을 때 **사용자에게** 보여 줄 문장.
#:
#: ⚠️ **예외 메시지를 그대로 내보내면 안 됩니다.** `StorageNotConfiguredError` 는 운영자가
#:    보라고 쓴 것이라 이슈 번호·환경 변수 이름·설정값이 들어 있습니다
#:    (예: "GAIT_BRIDGE_BASE_URL 에 경로가 붙어 있습니다: 'http://…'").
#:    실제로 앱 화면에 "…정책이 정해지면(#78) 열립니다" 가 그대로 뜬 적이 있습니다
#:    (2026-09-03, GCP 배포 직후 저장소 미설정 상태).
#:    진짜 사유는 로그로 남기고, 사용자에게는 이 한 줄만 보냅니다.
#:
#: ⚠️ **"잠시 뒤에 다시 시도해 주세요" 를 붙이지 않습니다.** 저장소 미설정은 몇 분 뒤에
#:    풀리는 게 아니라 서버 설정을 해야 풀립니다 — 재시도를 권하면 같은 화면을 계속
#:    보게 됩니다. 앱이 "보행 기능 꺼짐" 에 쓰는 문구와 **같은 말**이라, 서버가
#:    미설정이든 앱에서 꺼 뒀든 사용자는 같은 안내를 받습니다.
_STORAGE_NOT_READY = "보행 분석은 아직 준비 중이에요."


def _storage_unavailable(exc: StorageNotConfiguredError) -> HTTPException:
    """503 으로 바꾸면서 **사유는 로그에만** 남깁니다."""
    log.warning("보행 저장소가 준비되지 않았습니다: %s", exc)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _STORAGE_NOT_READY)


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
        raise _storage_unavailable(exc) from None
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
        raise _storage_unavailable(exc) from None
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
        overlay_url=_overlay_url(record),
    )


def _overlay_url(record: GaitRecord) -> str | None:
    """overlay 재생 주소. 없거나 저장소 미설정이면 None — 앱은 그때 원본을 재생합니다.

    다운로드는 인증 헤더 없는 bridge 를 지납니다(overlay 키는 backend 만 발급하는 uuid4 라
    추측 불가). 그래서 이 응답 자체가 소유권 게이트입니다 — 남의 record_id 는 위에서
    이미 404 이고, 여기까지 온 것은 내 기록의 overlay 뿐입니다.
    """
    if record.overlay_storage_key is None:
        return None
    try:
        return get_storage().download_url(
            record.overlay_storage_key,
            expires_in_seconds=settings.gait_download_url_ttl_seconds,
        )
    except StorageNotConfiguredError:
        # overlay 는 있는데 저장소가 안 켜진 상태 — 앱에는 원본 재생으로 조용히 물러납니다.
        return None


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


@router.post("/compare", response_model=GaitCompareResponse)
async def compare_records_endpoint(
    user: CurrentAppUser, session: Session, req: GaitCompareRequest
) -> GaitCompareResponse:
    """같은 반려견의 두 기록 비교 (D-058).

    **DB 에 저장된 분석 데이터만 씁니다** — 원본 영상도 overlay 도 읽지 않습니다.
    그래서 저장소가 무엇이든(local·gcs·미설정) 이 엔드포인트는 그대로 돕니다.

    두 진입 경로가 같은 계약을 씁니다: 방금 분석한 기록 ↔ 과거 기록(A), 저장된 과거
    기록끼리(B). **순서는 상관없습니다** — 날짜가 오래된 쪽이 past 가 됩니다.

    ⚠️ **없는 기록과 남의 기록은 같은 404 입니다.** 400 은 "내 기록 둘인데 비교 규칙에
       안 맞는 경우"(같은 기록·다른 반려견)라, 존재 여부가 새지 않습니다.
    """
    try:
        result = await gait_service.compare(
            session, user.app_user_id, req.record_id_a, req.record_id_b
        )
    except gait_service.NotFoundError:
        raise _NOT_FOUND from None
    except gait_service.CompareError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    return GaitCompareResponse(**result)


# ── 임시 bridge (gait_storage="local" 전용) ─────────────────────────────
#
# ⚠️ **여기가 운영 경로입니다** (D-052). 영상·사진의 모든 바이트가 이 두 엔드포인트를
#    지납니다 — D-043 원칙 1("영상이 backend 를 안 지난다")을 D-052 가 폐기했습니다.
#    그래서 업로드는 반드시 **스트리밍 + 크기 상한**입니다. 통째로 읽으면 영상 하나가
#    그대로 컨테이너 메모리입니다.
#    (`gait_storage="gcs"` 로 되돌리면 등록만 되어 있고 아무 키도 안 받습니다 —
#     아래 `_local_bridge` 가 404.)
#
# ⚠️ **인증 헤더를 요구하지 않습니다 — 대신 키가 자격입니다.** Signed URL 을 흉내 내는
#    자리라, 헤더를 요구하면 GCS 로 바꿀 때 앱 코드가 또 바뀝니다. 그래서 대신
#    **backend 가 실제로 발급한 키인지**를 DB 로 확인합니다 (find_by_storage_key).
#    이게 없으면 아무나 임의 경로로 서버 디스크를 채울 수 있습니다 — 실제로 그 상태로
#    한 번 배포됐고(2026-09-02), 이 검사가 그것을 막습니다.


def _local_bridge():
    from daengs_backend.core.storage import LocalBridgeStorage

    storage = get_storage()
    if not isinstance(storage, LocalBridgeStorage):
        # gcs/none 모드에서는 이 경로가 없는 것처럼 404.
        raise _NOT_FOUND
    return storage


#: 상한을 넘겼을 때 앱에 보내는 문장. nginx 가 먼저 끊으면 앱은 이 문장 대신
#: nginx 의 HTML 을 받습니다 — 그래서 settings 값이 nginx 200m 보다 낮아야 합니다.
_TOO_LARGE = "영상이 너무 큽니다."


@router.put("/_bridge/upload/{storage_key:path}", include_in_schema=False)
async def _bridge_upload(session: Session, storage_key: str, request: Request):
    """발급된 PENDING 키 하나에만 영상을 받습니다. **디스크로 흘려 씁니다.**"""
    from fastapi import Response

    storage = _local_bridge()
    # 발급된 적 없는 키 · 이미 confirm 된 키 = 없는 경로와 같은 404.
    # (PENDING 으로 좁히므로 confirm 뒤 덮어쓰기도 막힙니다.)
    if await gait_repo.find_by_storage_key(session, storage_key, status="PENDING") is None:
        raise _NOT_FOUND

    limit = settings.gait_max_upload_bytes

    # 큰 파일을 다 받고 나서 거절하면 대역폭과 디스크를 이미 쓴 뒤입니다.
    # Content-Length 는 앱이 주는 값이라 **믿지 않고**, 아래 누적 검사로 한 번 더 봅니다.
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_size = int(declared)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Content-Length가 올바르지 않습니다."
            ) from None
        if declared_size > limit:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, _TOO_LARGE)

    # 예외가 나면 open_write 가 반쯤 쓴 파일을 지웁니다 — 특히 0바이트로 남으면
    # redact() 의 tombstone 과 구별되지 않습니다.
    written = 0
    with storage.open_write(storage_key) as stream:
        async for chunk in request.stream():
            written += len(chunk)
            if written > limit:
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, _TOO_LARGE)
            stream.write(chunk)
        if written == 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "빈 영상은 업로드할 수 없습니다."
            )
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
