"""보행 분석 record/job lifecycle (D-043).

상태 전이의 전체 그림 — **전이는 이 파일만 합니다**:

    analyze 요청  : (없음)      → PENDING     + 업로드 티켓 발급
    confirm       : PENDING     → UPLOADED    + 스토리지 실존 확인 + 큐 발행
    워커 시작     : UPLOADED    → PROCESSING
    워커 끝       : PROCESSING  → DONE / FAILED

`quality_status`(ok/unavailable) 는 DONE 안에서의 축입니다 — FAILED(재시도)와
unavailable(재촬영)을 섞지 않습니다.

commit 은 저장소 규칙대로 services 계층(여기)에서 합니다.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.storage import get_storage
from daengs_backend.models.gait_record import GaitRecord
from daengs_backend.repositories import gait_record as gait_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.schemas.gait import GaitAnalyzeRequest

log = logging.getLogger(__name__)

# 업로드 티켓 유효시간. 영상이 커서 사진(스크리닝)보다 길게 잡습니다.
UPLOAD_TICKET_TTL_SECONDS = 15 * 60


class NotFoundError(LookupError):
    """없는 것과 남의 것 — 같은 예외입니다 (pet 서비스와 같은 규칙)."""


class WrongStateError(RuntimeError):
    """지금 상태에서 허용되지 않는 전이. 라우터가 409 로 옮깁니다."""


async def start_analysis(
    session: AsyncSession, app_user_id: uuid.UUID, req: GaitAnalyzeRequest
):
    """소유권 확인 → PENDING 기록 생성 → 업로드 티켓.

    반환: (record, ticket). 스토리지가 미설정이면 **기록을 만들기 전에** 실패합니다 —
    티켓 없는 PENDING 은 앱이 어찌할 수 없는 쓰레기 행입니다.
    """
    pet = await pet_repo.get_owned(session, app_user_id, req.pet_id)
    if pet is None:
        raise NotFoundError("pet")

    ticket = get_storage().create_upload_ticket(
        key_hint=f"gait/{req.pet_id}/{uuid.uuid4().hex}",
        content_type=req.content_type,
    )

    record = GaitRecord(
        pet_id=req.pet_id,
        status="PENDING",
        original_storage_key=ticket.storage_key,
        captured_at=req.captured_at,
        source_file=req.source_file,
        note=req.note,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record, ticket


async def confirm_upload(
    session: AsyncSession, app_user_id: uuid.UUID, record_id: uuid.UUID
) -> GaitRecord:
    """앱이 "올렸어" — 실존 확인 후 큐에 발행합니다.

    ⚠️ **앱의 말만 믿지 않습니다.** `exists()` 로 실제로 올라왔는지 봅니다 — 안 보면
       빈 기록이 PROCESSING 으로 넘어가 워커가 없는 파일을 받으러 갑니다.
    """
    record = await gait_repo.get_owned(session, app_user_id, record_id)
    if record is None:
        raise NotFoundError("record")
    if record.status != "PENDING":
        raise WrongStateError(f"confirm 은 PENDING 에서만 됩니다 (지금 {record.status})")

    if not get_storage().exists(record.original_storage_key):
        raise WrongStateError("업로드된 파일을 찾을 수 없습니다 — 업로드가 끝났는지 확인하세요")

    record.status = "UPLOADED"
    await session.commit()

    # 발행은 commit **뒤**입니다 — 앞이면 워커가 UPLOADED 가 되기 전의 행을 봅니다.
    from daengs_backend.tasks.gait import analyze

    analyze.delay(str(record.id))
    return record


async def soft_delete(
    session: AsyncSession, app_user_id: uuid.UUID, record_id: uuid.UUID
) -> GaitRecord:
    """지우기로 표시만 합니다. 스토리지 파일 정리는 #78 뒤 비동기로 —
    행을 먼저 지우면 storage_key 를 잃어 파일이 영영 고아가 됩니다."""
    record = await gait_repo.get_owned(session, app_user_id, record_id)
    if record is None:
        raise NotFoundError("record")
    from sqlalchemy import func

    record.deleted_at = func.now()
    await session.commit()
    return record


# ── 워커 쪽 (별도 프로세스에서만 실행됩니다) ─────────────────────────────


def run_analysis_sync(record_id: str) -> None:
    """Celery 태스크 본문. 워커는 이벤트 루프가 없어 여기서 하나 엽니다."""
    asyncio.run(_run_analysis(uuid.UUID(record_id)))


async def _run_analysis(record_id: uuid.UUID) -> None:
    from sqlalchemy import select

    from daengs_backend.core.database import SessionLocal

    async with SessionLocal() as session:
        record = (
            await session.execute(select(GaitRecord).where(GaitRecord.id == record_id))
        ).scalar_one_or_none()
        if record is None or record.deleted_at is not None:
            log.warning("gait.analyze: 기록이 없거나 삭제됨 record_id=%s", record_id)
            return
        if record.status != "UPLOADED":
            # 재전달(acks_late)로 두 번 올 수 있습니다 — 이미 처리됐으면 조용히 끝냅니다.
            log.info("gait.analyze: 건너뜀 status=%s record_id=%s", record.status, record_id)
            return

        record.status = "PROCESSING"
        await session.commit()

        try:
            result = await asyncio.to_thread(_analyze_from_storage, record.original_storage_key)
        except Exception as exc:  # noqa: BLE001 — 실패 사유를 행에 남기는 것이 목적입니다
            record.status = "FAILED"
            record.failure_reason = str(exc)[:2000]
            await session.commit()
            log.error("gait.analyze 실패 record_id=%s: %s", record_id, exc)
            return

        record.status = "DONE"
        record.quality_status = result["quality"].get("status")
        record.quality_tier = result["quality"].get("quality_tier")
        record.quality = result["quality"]
        record.summary_for_ui = (result.get("features") or {}).get("summary_for_ui")
        record.internal_feature_vector = (result.get("features") or {}).get(
            "internal_feature_vector"
        )
        record.gait_filter_version = result.get("gait_filter_version")
        record.video_meta = result.get("video_meta")
        record.overlay_storage_key = result.get("overlay_storage_key")
        await session.commit()


def _analyze_from_storage(storage_key: str) -> dict:
    """스토리지에서 받아 분석합니다 — **무거운 것은 전부 여기서 지연 import** (ⓒ).

    이 함수는 gait 그룹(torch·ultralytics)이 설치된 워커에서만 불립니다.
    backend 웹 프로세스는 태스크를 발행만 하므로 이 import 에 절대 닿지 않습니다.

    ⚠️ #78 전에는 스토리지가 미설정이라 여기 도달하면 StorageNotConfiguredError 로
       FAILED 가 됩니다 — 의도된 명확한 실패입니다.
    """
    import tempfile
    from pathlib import Path
    from urllib.request import urlretrieve

    from daengs_backend.core.storage import get_storage

    url = get_storage().download_url(storage_key, expires_in_seconds=600)

    with tempfile.TemporaryDirectory() as td:
        local = Path(td) / "input.bin"
        urlretrieve(url, local)  # noqa: S310 — 우리 스토리지가 발급한 서명 URL 입니다

        from daengs_gait.pipeline import process_video  # 지연 — torch 가 여기서 올라옵니다

        record = process_video(local)
        # overlay 업로드는 #78 뒤에 — 지금은 키 없이 반환합니다.
        record["overlay_storage_key"] = None
        return record
