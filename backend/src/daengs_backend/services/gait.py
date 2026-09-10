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

from daengs_backend.core.storage import (
    build_object_key,
    build_overlay_object_key,
    get_storage,
)
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


class BrokerUnavailable(RuntimeError):
    """브로커 주소가 없거나 브로커에 묻지 못함. 상태 페이지는 이것을 `absent` 로 읽습니다."""


#: 워커가 듣는 큐. `tasks/gait.py` 의 `task_default_queue` 와 같아야 합니다 —
#: `tests/test_gait_worker_status.py` 가 둘을 대조합니다.
GAIT_QUEUE = "gait"


def gait_workers(timeout_sec: float = 1.0) -> list[str]:
    """`gait` 큐를 듣고 있는 Celery 워커 이름들. 없으면 빈 목록입니다 (상태 페이지, D-063 4단계).

    옛 `gait-analysis` HTTP 서비스의 `/healthz` 를 대신합니다 — 이제 보행 분석의 실행부는
    `gait-worker` 하나뿐이라, "살아 있나" 는 그 워커가 큐를 듣고 있나로 묻습니다.

    `crawl_workers` 의 celery 갈래와 같은 모양입니다. `ping()` 이 아니라 `active_queues()` 를
    쓰는 이유도 같습니다 — 이 브로커에는 크롤러·실시간 워커도 붙어 있어서 ping 은
    **보행 워커가 아닌 답**을 보행 워커로 읽습니다. 큐 이름으로 걸러야 합니다.

    브로커 주소가 없거나 브로커가 안 답하면 `BrokerUnavailable` 입니다 — 500 이 아니라
    "이 환경엔 없다" 입니다. **동기입니다** (kombu). 부르는 쪽이 스레드로 돌립니다.
    """
    from daengs_backend.config import settings

    if not settings.redis_url:
        raise BrokerUnavailable("REDIS_URL 이 없다 — backend/.env 를 확인할 것")
    from celery import Celery

    app = Celery(broker=settings.redis_url)      # 보내기 전용 — 이 프로세스는 워커가 아닙니다
    try:
        replies = app.control.inspect(timeout=timeout_sec).active_queues()
    except Exception as e:                      # 브로커가 죽은 것은 500 이 아니다
        raise BrokerUnavailable(f"브로커에 묻지 못했다: {type(e).__name__}: {e}") from e

    # 아무도 답하지 않으면 None 입니다 (빈 dict 가 아닙니다).
    if not replies:
        return []
    return sorted(
        node
        for node, queues in replies.items()
        if any(q.get("name") == GAIT_QUEUE for q in queues or [])
    )


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

    # ⚠️ object key 는 **backend 가 만듭니다** (원칙 6) — 앱이 못 정합니다. 앱은
    #    source_file(표시용 이름)만 주고, 그 확장자만 키에 반영됩니다.
    object_key = build_object_key(req.pet_id, kind="original", source_file=req.source_file)
    ticket = get_storage().create_upload_ticket(
        object_key=object_key, content_type=req.content_type
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
    """행을 잠근 채 object 를 먼저 지우고, 성공한 뒤 기록을 지웁니다.

    PostgreSQL 과 object storage 사이에 원자성을 주장하지 않습니다. object 삭제 뒤
    DB commit 이 실패하면 행과 키가 남아 재시도할 수 있고, 이미 없는 object 삭제는
    성공입니다. 반대로 object 삭제가 실패하면 행을 지우지 않아 키를 잃지 않습니다.

    ⚠️ **파기 정책(보관 기간·탈퇴 시점)은 아직 #78 대기입니다.** 이 함수는 사용자가
       **직접 삭제**를 눌렀을 때의 자리이고, 자동 파기는 별도 스케줄이 같은 cleanup
       경로를 호출하는 것으로 붙습니다 (아래 collect_orphans 참고).
    """
    record = await gait_repo.get_owned(
        session, app_user_id, record_id, for_update=True
    )
    if record is None:
        raise NotFoundError("record")

    try:
        await _delete_record_objects(record)
        await session.delete(record)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return record


class CompareError(RuntimeError):
    """비교할 수 없는 요청. 라우터가 400 으로 옮깁니다 (없는 것과 구분됩니다)."""


def _as_compare_record(record: GaitRecord) -> dict:
    """DB 행을 `compare_records` 가 읽는 모양으로 맞춥니다 (D-058).

    ⚠️ **파일을 하나도 안 만집니다.** 옛 구현은 `load_record()` 로 JSON 파일을 읽었지만,
       비교에 필요한 값은 전부 DB 컬럼에 있습니다 — 원본·overlay 는 보관/재생용이고
       비교의 기준 데이터가 아닙니다. 그래서 저장소 구현이 무엇이든(local·gcs) compare 는
       그대로 돕니다.

    `compare_records` 가 실제로 읽는 것만 채웁니다 — 전수 확인한 목록입니다:
    `record_id` · `date` · `quality.status` · `quality.quality_tier` ·
    `quality.recommendation` · `features.summary_for_ui` ·
    `features.internal_feature_vector` · `gait_filter_version` · `pose_model`.

    ⚠️ `quality_tier` 는 `quality` **dict 안**에서 읽습니다 (`quality_gate` 가 거기 넣고
       서비스가 통째로 저장합니다). 별도 컬럼도 있지만 그쪽을 쓰면 두 값이 갈릴 수 있어
       저장된 dict 하나만 봅니다.

    `pose_model` 은 기록이 어떤 관절 정의로 만들어졌는지입니다 (D-063). v4 compare 가 자기
    가드(`pm_a != pm_b`)에서 읽는 키이기도 합니다 — 안 넣으면 그 가드가 기본값 `best_pt`
    로 늘 통과해 버립니다.
    """
    return {
        "record_id": str(record.id),
        "date": record.captured_at.isoformat() if record.captured_at else None,
        "quality": record.quality or {},
        "features": {
            "summary_for_ui": record.summary_for_ui or {},
            "internal_feature_vector": record.internal_feature_vector or {},
        },
        "gait_filter_version": record.gait_filter_version,
        "pose_model": record.pose_model,
    }


def _order_by_age(a: GaitRecord, b: GaitRecord) -> tuple[GaitRecord, GaitRecord]:
    """(past, recent) — **오래된 쪽이 past** 입니다.

    앱이 어느 순서로 골랐든 결과가 같아야 합니다. 그래서 정렬을 앱이 아니라 여기서
    합니다 — 앱에 맡기면 A 진입(기준 기록 먼저)과 B 진입(둘 다 고름)이 서로 다른
    순서를 보내고, 화면의 "이전/최근" 라벨이 뒤집힙니다.

    촬영일(`captured_at`)이 없을 수 있어 그때는 만들어진 시각으로 갈음합니다.
    """
    def key(r: GaitRecord):
        return (r.captured_at or r.created_at.date(), r.created_at)

    return (a, b) if key(a) <= key(b) else (b, a)


async def compare(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    record_id_a: uuid.UUID,
    record_id_b: uuid.UUID,
) -> dict:
    """두 기록 비교. **DB 데이터만으로 완결됩니다** (D-058).

    판정·임계값·문구는 각 모델의 비교 함수 그대로입니다 — 여기서는 입력을 모아 주고,
    **두 기록의 `pose_model` 로 비교 가능 여부**를 가른 뒤, `_dev_only_*` 만 걷어냅니다.

    비교 불가는 오류가 아니라 정상 상태이고, "같은 기록"·"다른 반려견" 과 같은 통로
    (`CompareError` → 400 + 사유)로 나갑니다. NULL 을 같은 모델로 보지 않습니다 —
    모델을 모르는 기록끼리는 관절 정의가 같다는 보장이 없습니다.
    """
    if record_id_a == record_id_b:
        raise CompareError("같은 기록끼리는 비교할 수 없습니다.")

    rows = await gait_repo.get_owned_pair(session, app_user_id, (record_id_a, record_id_b))
    if len(rows) != 2:
        # 없는 것과 남의 것을 구분하지 않습니다 (이 모듈의 규칙).
        raise NotFoundError("record")

    first, second = rows
    if first.pet_id != second.pet_id:
        # 소유자는 같지만 **다른 반려견**입니다. 개체가 다르면 비교가 의미를 잃습니다 —
        # 이 서비스는 "같은 아이의 시간 변화"를 보는 것이라서요.
        raise CompareError("서로 다른 반려견의 기록은 비교할 수 없습니다.")

    # 관절 정의 호환성 (D-063). 서버가 지금 어떤 엔진을 돌리는지(`GAIT_ENGINE`)는 보지 않습니다 —
    # 기록이 무엇으로 만들어졌는지가 기준입니다.
    if first.pose_model is None or second.pose_model is None:
        raise CompareError("비교 불가 — 분석 모델 정보가 없는 기록입니다.")
    if first.pose_model != second.pose_model:
        raise CompareError("비교 불가 — 서로 다른 분석 모델로 만든 기록입니다.")

    past, recent = _order_by_age(first, second)

    # 비교 함수 선택과 `_dev_only_*` 제거는 `_run_compare` 에 있습니다.
    return _run_compare(
        _as_compare_record(past), _as_compare_record(recent), pose_model=first.pose_model
    )


# ── 정리 (워커/스케줄) ──────────────────────────────────────────────────


def run_cleanup_sync(record_id: str) -> None:
    asyncio.run(_cleanup(uuid.UUID(record_id)))


async def _cleanup(record_id: uuid.UUID) -> None:
    """soft delete 된 기록의 GCS object(원본·overlay)를 지웁니다.

    삭제가 끝나면 행도 물리 삭제합니다 — deleted_at 이 찍힌 뒤라 소유권 조회에는
    이미 안 잡히고, 파일이 사라진 행을 남겨 둘 이유가 없습니다.
    """
    from sqlalchemy import delete, select

    from daengs_backend.core.database import worker_session

    # ⚠️ SessionLocal 이 아니라 worker_session 입니다 — 이유는 그 함수 docstring 참고
    #    (이 함수가 바로 그 버그로 서버에서 실패했습니다).
    async with worker_session() as session:
        record = (
            await session.execute(
                select(GaitRecord)
                .where(GaitRecord.id == record_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if record is None:
            return
        if record.deleted_at is None:
            log.warning("gait.cleanup: 삭제 표시가 없는 기록 record_id=%s — 건너뜀", record_id)
            return

        try:
            await _delete_record_objects(record)
        except Exception as exc:  # noqa: BLE001 — 행과 키를 보존해 다음 정리가 재시도합니다
            log.error("gait.cleanup: object 삭제 실패 record_id=%s: %s", record_id, exc)
            await session.rollback()
            return

        await session.execute(delete(GaitRecord).where(GaitRecord.id == record_id))
        await session.commit()


def _cleanup_keys(record: GaitRecord) -> tuple[str, ...]:
    """저장된 키와 DB commit 실패 때도 계산 가능한 overlay 키를 모두 돌려줍니다."""
    keys = (
        record.original_storage_key,
        record.overlay_storage_key,
        build_overlay_object_key(record.pet_id, record.id),
    )
    return tuple(dict.fromkeys(key for key in keys if key))


async def _delete_record_objects(record: GaitRecord) -> None:
    storage = get_storage()
    for key in _cleanup_keys(record):
        storage.delete(key)


async def cleanup_for_pets(
    session: AsyncSession, pet_ids: list[uuid.UUID]
) -> list[GaitRecord]:
    """pet 삭제와 같은 트랜잭션에서 모든 gait object 를 먼저 정리합니다.

    gait 행을 ``FOR UPDATE`` 로 잠근 채 원본·저장된 overlay·결정적 overlay 후보를
    삭제합니다. 모든 삭제가 성공해야 호출자가 pet 삭제로 진행할 수 있습니다. 행 자체는
    여기서 지우지 않습니다. pet commit 의 FK CASCADE 가 마지막에 지우므로, 그 전까지
    재시도에 필요한 키가 DB 에 보존됩니다.
    """
    records = await gait_repo.list_for_pets_for_update(session, pet_ids)
    for record in records:
        await _delete_record_objects(record)
    return records


async def collect_orphans(session: AsyncSession, *, older_than_minutes: int) -> list[uuid.UUID]:
    """confirm 이 오지 않아 PENDING 에 머문 기록을 찾습니다 (원칙 10).

    앱이 티켓만 받고 업로드/confirm 을 안 하면(또는 업로드 후 죽으면) PENDING 행과
    (혹시 올라갔다면) object 가 고아로 남습니다. 스케줄이 이 목록을 받아 cleanup 을
    발행합니다.

    ⚠️ **몇 분 뒤에 고아로 볼지는 #78 이 정할 값입니다** — 여기서는 인자로만 받습니다.
       탈퇴/보관기간 만료 파기도 같은 통로(cleanup 발행)로 붙습니다.
    """
    import datetime

    from sqlalchemy import select

    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        minutes=older_than_minutes
    )
    stmt = select(GaitRecord.id).where(
        GaitRecord.status == "PENDING",
        GaitRecord.created_at < cutoff,
        GaitRecord.deleted_at.is_(None),
    )
    return list((await session.execute(stmt)).scalars())


# ── 워커 쪽 (별도 프로세스에서만 실행됩니다) ─────────────────────────────


def run_analysis_sync(record_id: str) -> None:
    """Celery 태스크 본문. 워커는 이벤트 루프가 없어 여기서 하나 엽니다."""
    asyncio.run(_run_analysis(uuid.UUID(record_id)))


async def _run_analysis(record_id: uuid.UUID) -> None:
    from sqlalchemy import select

    from daengs_backend.core.database import worker_session

    # ⚠️ SessionLocal 이 아닙니다 — 워커의 두 번째 태스크부터 이벤트 루프가 갈립니다
    #    (core/database.worker_session docstring).
    async with worker_session() as session:
        record = (
            await session.execute(
                select(GaitRecord)
                .where(GaitRecord.id == record_id)
                .with_for_update()
            )
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
            result = await asyncio.to_thread(
                _analyze_from_storage, record.original_storage_key
            )
        except Exception as exc:  # noqa: BLE001 — 실패 사유를 행에 남기는 것이 목적입니다
            record = (
                await session.execute(
                    select(GaitRecord)
                    .where(GaitRecord.id == record_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if record is None or record.deleted_at is not None:
                log.info("gait.analyze: 분석 실패 뒤 기록이 삭제됨 record_id=%s", record_id)
                return
            record.status = "FAILED"
            record.failure_reason = str(exc)[:2000]
            await session.commit()
            log.error("gait.analyze 실패 record_id=%s: %s", record_id, exc)
            return

        # 삭제도 같은 행을 FOR UPDATE 로 잡습니다. 삭제가 먼저 잠갔으면 pet CASCADE 뒤
        # 행이 사라져 여기서 upload 하지 않고, 완료가 먼저 잠갔으면 삭제가 commit 을
        # 기다렸다가 방금 저장한 overlay 키까지 읽어 정리합니다.
        record = (
            await session.execute(
                select(GaitRecord)
                .where(GaitRecord.id == record_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if record is None or record.deleted_at is not None:
            log.info("gait.analyze: 완료 전에 기록이 삭제됨 record_id=%s", record_id)
            return
        if record.status != "PROCESSING":
            log.info(
                "gait.analyze: 완료 반영 건너뜀 status=%s record_id=%s",
                record.status,
                record_id,
            )
            return

        # 계약 검사는 **경고만** 냅니다 (daengs_gait.contract). 여기서 예외를 내면 DONE 이
        # 못 되고 행이 FAILED/좀비가 되는데, 계약 위반은 사고를 만들 게 아니라 발견할 일입니다.
        from daengs_gait.contract import check_analysis_record  # 가벼운 모듈 (numpy 없음)

        problems = check_analysis_record(result)
        if problems:
            log.warning(
                "gait.analyze: 엔진 출력이 계약과 어긋남 record_id=%s: %s", record_id, problems
            )

        overlay_data = result.pop("_overlay_bytes", None)
        overlay_key = None
        if overlay_data is not None:
            overlay_key = build_overlay_object_key(record.pet_id, record.id)
            storage = get_storage()
            try:
                if hasattr(storage, "upload_bytes"):
                    storage.upload_bytes(overlay_key, overlay_data, content_type="video/mp4")
                elif hasattr(storage, "write"):
                    storage.write(overlay_key, overlay_data)
            except Exception as exc:  # noqa: BLE001 — 결정적 키를 best-effort 로 되걷습니다
                try:
                    storage.delete(overlay_key)
                except Exception:
                    log.exception("gait.analyze: 실패한 overlay 정리도 실패 key=%s", overlay_key)
                record.status = "FAILED"
                record.failure_reason = str(exc)[:2000]
                # 엔진은 돌았으므로 어떤 모델이었는지는 안다 — 결과 없이 실패한 경우와 구분.
                record.pose_model = result.get("pose_model")
                await session.commit()
                log.error("gait.analyze overlay 업로드 실패 record_id=%s: %s", record_id, exc)
                return

        record.status = "DONE"
        # 어떤 pose model / 관절 정의로 만든 기록인가 (D-063). 두 엔진 다 품질 판정 전에
        # 넣으므로 unavailable 이어도 값이 있다. 검증하지 않고 그대로 저장 — CHECK 도 없다.
        record.pose_model = result.get("pose_model")
        record.quality_status = result["quality"].get("status")
        # ⚠️ 컬럼 CHECK(good/ok/low)는 엔진 어휘와 같아야 합니다. 2026-09-09 까지 CHECK 가
        #    good/low 뿐이라 20~80 구간(`ok`)의 DONE 커밋이 CheckViolation 으로 죽고 행이
        #    PROCESSING 으로 남았습니다(47.mp4 두 번 연속). CHECK 를 넓혀 값은 그대로 넣고,
        #    `_db_quality_tier` 는 CHECK 밖의 값만 None 으로 거릅니다(예외를 내면 다시 좀비).
        record.quality_tier = _db_quality_tier(result["quality"].get("quality_tier"))
        record.quality = result["quality"]
        record.summary_for_ui = (result.get("features") or {}).get("summary_for_ui")
        record.internal_feature_vector = (result.get("features") or {}).get(
            "internal_feature_vector"
        )
        record.gait_filter_version = result.get("gait_filter_version")
        record.video_meta = result.get("video_meta")
        record.overlay_storage_key = overlay_key
        record.failure_reason = None
        try:
            await session.commit()
        except Exception as exc:
            await session.rollback()
            if overlay_key is not None:
                try:
                    get_storage().delete(overlay_key)
                except Exception:
                    log.exception(
                        "gait.analyze: DB commit 실패 뒤 overlay 정리 실패 key=%s",
                        overlay_key,
                    )
            # ⚠️ 여기서 그냥 raise 만 하면 행이 **PROCESSING 으로 영원히 남습니다** — DONE 전이가
            #    방금 롤백됐고, acks_late 재전달은 `status != "UPLOADED"` 라 건너뛰기 때문입니다.
            #    2026-09-09 에 CheckViolation(quality_tier) 으로 실제로 두 건이 그렇게 갇혔습니다.
            #    실패는 실패로 적어야 앱이 "다시 시도" 를 띄우고 사람이 원인을 봅니다.
            await _mark_failed_after_commit_error(session, record_id, exc)
            raise


async def _mark_failed_after_commit_error(session, record_id: uuid.UUID, exc: Exception) -> None:
    """DONE 커밋이 실패한 행을 **새 트랜잭션**에서 FAILED 로 닫습니다.

    best-effort 입니다 — 여기서 또 실패하면 로그만 남기고 원래 예외를 살립니다. 원인이
    DB 자체(연결 끊김 등)면 이것도 안 되지만, 제약 위반처럼 **값 문제**면 이 UPDATE 는
    (그 값을 안 쓰므로) 통과해서 좀비를 막습니다.
    """
    from sqlalchemy import select

    try:
        row = (
            await session.execute(
                select(GaitRecord)
                .where(GaitRecord.id == record_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if row is None or row.status != "PROCESSING":
            return
        row.status = "FAILED"
        row.failure_reason = f"결과 저장 실패: {str(exc)[:1900]}"
        await session.commit()
    except Exception:
        await session.rollback()
        log.exception("gait.analyze: 커밋 실패 뒤 FAILED 표시도 실패 record_id=%s", record_id)


def _analyze_from_storage(storage_key: str) -> dict:
    """스토리지에서 받아 분석하고 overlay bytes 를 메모리로 돌려줍니다 —
    **무거운 것은 전부 여기서 지연 import** (ⓒ).

    이 함수는 gait 그룹(torch·ultralytics)이 설치된 워커에서만 불립니다. backend 웹
    프로세스는 태스크를 발행만 하므로 이 import 에 절대 닿지 않습니다.

    ⚠️ 저장소가 미설정(none)이면 여기 도달하기 전에 confirm 이 이미 막습니다. gcs 인데
       자격증명이 없으면 download 에서 실패해 FAILED 가 됩니다 — 의도된 명확한 실패입니다.

    순서: 다운로드 → `intake.prepare_for_analysis`(판정) → 엔진 → overlay bytes. 임시 디렉터리는
    이 함수가 유일하게 소유하고, 판정·변환·overlay 전부 그 안에서만 파일을 만듭니다.
    """
    import tempfile
    from pathlib import Path
    from urllib.request import urlretrieve

    from daengs_backend.config import settings
    from daengs_backend.core.storage import get_storage

    storage = get_storage()

    with tempfile.TemporaryDirectory() as td:
        local = Path(td) / "input.bin"
        if hasattr(storage, "local_path"):
            # LocalBridge — HTTP 없이 파일을 바로 씁니다 (워커·backend 가 같은 볼륨).
            local.write_bytes(storage.local_path(storage_key).read_bytes())
        else:
            # GCS — Signed URL 로 받습니다.
            url = storage.download_url(
                storage_key, expires_in_seconds=settings.gait_download_url_ttl_seconds
            )
            urlretrieve(url, local)

        # 입력 판정 (D-063 3단계): 읽을 수 있으면 원본 그대로, 못 읽을 때만 H.264 로 변환,
        # 그래도 못 읽으면 VideoDecodeError → 바깥 except 가 FAILED + 사유로 닫습니다.
        # 변환본은 같은 임시 디렉터리 안에 생기고 원본은 지워지므로 정리는 그대로입니다.
        from daengs_gait.intake import prepare_for_analysis

        local = prepare_for_analysis(local)

        # 엔진 선택과 실행은 daengs_gait 의 몫입니다 (D-063 2단계). 설정값은 인자로 넘깁니다 —
        # daengs_gait 는 daengs_backend 를 import 하지 않습니다. legacy 는 그 안에서
        # torch 를 지연 import 하고, v4 는 별도 venv 의 서브프로세스라 여기엔 안 올라옵니다.
        from daengs_gait.engines import get_engine

        engine = get_engine(
            settings.gait_engine,
            v4_dir=settings.gait_v4_dir,
            v4_python=settings.gait_v4_python,
        )
        record = engine.analyze(local)

        # 업로드는 DB 행 잠금을 잡은 _run_analysis 가 합니다. 여기서 먼저 올리면 탈퇴
        # cleanup 과 경합해 새 고아 object 를 만들 수 있습니다.
        overlay_path = record.pop("overlay_video", None)
        overlay_data = None
        if overlay_path and Path(overlay_path).exists():
            overlay_data = Path(overlay_path).read_bytes()
        record["_overlay_bytes"] = overlay_data
        return record


#: `gait_records.quality_tier` 의 CHECK 가 허용하는 값 (db/init/07_gait_records.sql).
#: 엔진(legacy `daengs_gait/quality_gate.py` · v4 `gait_v4/quality.py`)이 내는 어휘와
#: **같아야 합니다** — `tests/test_gait_quality_tier_contract.py` 가 두 엔진 소스와 SQL 을
#: 실제로 읽어 이 상수까지 대조합니다.
DB_QUALITY_TIERS = frozenset({"good", "ok", "low"})


def _db_quality_tier(raw: str | None) -> str | None:
    """엔진이 낸 tier 를 **그대로** 컬럼에 넣되, CHECK 밖의 값만 걸러냅니다.

    두 엔진 다 유효 프레임 수(`n_frames_gait_usable`)로 세 단계를 냅니다:

        good  81 이상      quality_note 없음
        ok    20 ~ 80      quality_note 붙음 (80 미만이라 참고용)
        low    4 ~ 19      quality_note 붙음
        (4 미만은 status=unavailable, tier 없음)

    앱(`GaitQualityTier`)도 같은 세 값을 각각 다른 문장·색으로 그리므로 **변환하지 않습니다.**
    한때 `ok → low` 로 접는 안이 있었는데, 그건 앱이 만든 "보통" 문장을 못 쓰게 하는
    격하라 버렸습니다 (2026-09-09).

    모르는 값이 오면 None 으로 둡니다 — 컬럼이 nullable 이라 CHECK 를 지나고, 앱은
    `effectiveTier` 로 보정합니다. 여기서 예외를 내면 DONE 커밋이 죽어 다시 좀비가 됩니다.
    """
    if raw in DB_QUALITY_TIERS:
        return raw
    if raw is not None:
        log.warning("gait.analyze: CHECK 밖의 quality_tier=%r → None 으로 저장", raw)
    return None


# ── 비교 엔진 선택 (D-058 · D-063 2단계) ───────────────────────────────────
#
# 분석 엔진의 실행 배관(v4 서브프로세스 · legacy 지연 import)은 `daengs_gait.engines` 로
# 옮겼습니다. 비교는 여기 남습니다 — 비교 함수를 고르는 기준이 **서버 설정이 아니라 두
# 기록의 `pose_model`** 이기 때문입니다. 서버가 v4 로 바뀐 뒤에도 legacy 기록 둘은 legacy
# 판정으로, 관절 정의가 다른 둘은 비교 불가로 가야 합니다.


def _load_v4_compare():
    """`gait_v4/compare.py` 를 **파일로** 불러옵니다.

    `import gait_v4.compare` 는 안 됩니다 — 패키지 `__init__` 이 `analyze` → `pose` →
    torch·onnxruntime 을 끌고 오는데 backend 웹 venv 에는 없습니다. compare.py 자체는
    numpy 만 쓰므로 모듈 하나만 파일에서 로드하면 웹 프로세스에서도 돕니다.
    판정 로직은 `daengs_gait.compare` 와 같고, `message_kind` · `side_summary` ·
    `condition_flags` 가 더 있습니다 (walk_demo 계약).
    """
    import importlib.util

    from daengs_backend.config import settings
    from daengs_gait.engines.v4 import resolve_dir

    path = resolve_dir(settings.gait_v4_dir) / "gait_v4" / "compare.py"
    if not path.exists():
        raise RuntimeError(f"gait_v4 compare 모듈이 없습니다: {path}")
    spec = importlib.util.spec_from_file_location("_daengs_gait_v4_compare", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.compare_records


def _run_compare(past: dict, recent: dict, *, pose_model: str) -> dict:
    """`pose_model` 에 맞는 비교 함수를 골라 돌리고 `_dev_only_*` 를 걷어냅니다.

    호출자(`compare`)가 두 기록의 `pose_model` 이 같고 NULL 이 아님을 이미 확인했습니다.
    여기서는 그 값으로 판정 코드를 고르기만 합니다 — **`settings.gait_engine` 은 보지
    않습니다.** 레지스트리(`contract.POSE_MODELS`)에 없는 값은 비교 불가입니다.

    `_dev_only_*` 는 **앱에 절대 내보내지 않습니다** — 수백 개의 숫자가 화면에 나오면
    사용자가 그것을 건강 점수로 읽습니다 (API.md 의 노출 금지 규칙). 두 엔진 다 같은
    접두사를 씁니다.
    """
    from daengs_gait.contract import POSE_MODEL_LEGACY, POSE_MODEL_V4

    if pose_model == POSE_MODEL_V4:
        compare_fn = _load_v4_compare()
    elif pose_model == POSE_MODEL_LEGACY:
        # ⚠️ 지연 import — numpy 를 끌고 옵니다. backend 웹 프로세스의 main import 를
        #    가볍게 유지하는 규율(D-021)이고, 비교를 안 부르면 안 올라옵니다.
        from daengs_gait.compare import compare_loaded_records as compare_fn
    else:
        raise CompareError(f"비교 불가 — 지원하지 않는 분석 모델입니다: {pose_model}")

    result = compare_fn(past, recent)
    return {k: v for k, v in result.items() if not k.startswith("_dev_only_")}
