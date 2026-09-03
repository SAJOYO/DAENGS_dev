"""점령지 방문 인증의 상태 전이와 트랜잭션 경계.

촬영 요청에서는 위치 10m만 동기적으로 판정하고, 사진 내용은 confirm 뒤
``VISION_PENDING``에 둡니다. VLM 워커는 나중에 ``record_vision_decision``만 호출하면
되며, 실제 점령/소유권은 이 모듈의 책임이 아닙니다.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.storage import (
    UploadTicket,
    build_territory_photo_key,
    get_storage,
)
from daengs_backend.models.territory import (
    TERRITORY_EVIDENCE_VERSION,
    TerritoryAttempt,
    VerifiedVisit,
)
from daengs_backend.repositories import territory as territory_repo
from daengs_backend.schemas.territory import TerritoryAttemptStart
from daengs_backend.services.territory_site_lookup import TerritorySiteLookup

CAPTURE_RADIUS_M = 10.0
MAX_TERRITORY_PHOTO_BYTES = 12 * 1024 * 1024
TERRITORY_BRIDGE_UPLOAD_PATH = "/app/territory/attempts/_bridge/upload"


class TerritoryAttemptNotFoundError(LookupError):
    """없는 시도와 남의 시도는 같은 예외입니다."""


class TerritoryAttemptConflictError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _haversine_m(
    lat_a: Decimal,
    lng_a: Decimal,
    lat_b: Decimal,
    lng_b: Decimal,
) -> float:
    lat1, lon1, lat2, lon2 = map(
        math.radians,
        (float(lat_a), float(lng_a), float(lat_b), float(lng_b)),
    )
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_008.8 * 2 * math.asin(math.sqrt(h))


def _same_capture(attempt: TerritoryAttempt, body: TerritoryAttemptStart) -> bool:
    return (
        attempt.client_session_id == body.client_session_id
        and attempt.site_id == body.site_id
        and attempt.captured_at == body.captured_at
        and attempt.capture_lat == body.lat
        and attempt.capture_lng == body.lng
        and attempt.accuracy_m == body.accuracy_m
        and attempt.is_mock == body.is_mock
        and attempt.photo_content_type == body.content_type
    )


def _ticket_for(attempt: TerritoryAttempt) -> UploadTicket | None:
    if attempt.status != "PENDING_UPLOAD":
        return None
    return get_storage().create_upload_ticket(
        object_key=attempt.photo_storage_key,
        content_type=attempt.photo_content_type,
        bridge_upload_path=TERRITORY_BRIDGE_UPLOAD_PATH,
        create_only=True,
    )


async def start_attempt(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    body: TerritoryAttemptStart,
    site_lookup: TerritorySiteLookup,
) -> tuple[TerritoryAttempt, UploadTicket | None, bool]:
    """10m 위치 증거를 고정하고 사진 업로드 티켓을 발급합니다."""
    existing = await territory_repo.get_by_client_capture(
        session, app_user_id, body.client_capture_id
    )
    if existing is not None:
        if not _same_capture(existing, body):
            raise TerritoryAttemptConflictError(
                "capture_id_conflict",
                "같은 client_capture_id에 다른 촬영 증거를 보낼 수 없습니다.",
            )
        return existing, _ticket_for(existing), False

    if body.is_mock:
        raise TerritoryAttemptConflictError(
            "mock_location",
            "모의 위치가 표시된 촬영은 방문 인증에 사용할 수 없습니다.",
        )

    site = await site_lookup.find_near_capture(
        site_id=body.site_id,
        lat=body.lat,
        lng=body.lng,
    )
    if site is None:
        raise TerritoryAttemptConflictError(
            "site_not_nearby",
            "촬영 위치 주변에서 요청한 현행 점령지를 찾을 수 없습니다.",
        )

    distance_m = _haversine_m(body.lat, body.lng, site.lat, site.lng)
    if distance_m > CAPTURE_RADIUS_M:
        raise TerritoryAttemptConflictError(
            "outside_capture_radius",
            f"점령지 인증 반경 10m 밖입니다 (현재 {distance_m:.1f}m).",
        )
    if distance_m + body.accuracy_m > CAPTURE_RADIUS_M:
        raise TerritoryAttemptConflictError(
            "insufficient_location_accuracy",
            "GPS 오차를 포함하면 점령지 인증 반경 10m를 벗어납니다 "
            f"(거리 {distance_m:.1f}m + 오차 {body.accuracy_m:.1f}m).",
        )

    attempt_id = uuid.uuid4()
    storage_key = build_territory_photo_key(
        app_user_id,
        attempt_id,
        content_type=body.content_type,
    )
    ticket = get_storage().create_upload_ticket(
        object_key=storage_key,
        content_type=body.content_type,
        bridge_upload_path=TERRITORY_BRIDGE_UPLOAD_PATH,
        create_only=True,
    )
    attempt = TerritoryAttempt(
        id=attempt_id,
        app_user_id=app_user_id,
        client_capture_id=body.client_capture_id,
        client_session_id=body.client_session_id,
        site_id=body.site_id,
        captured_at=body.captured_at,
        capture_lat=body.lat,
        capture_lng=body.lng,
        accuracy_m=body.accuracy_m,
        is_mock=False,
        site_lat=site.lat,
        site_lng=site.lng,
        distance_m=distance_m,
        status="PENDING_UPLOAD",
        photo_storage_key=storage_key,
        photo_content_type=body.content_type,
        photo_object_generation=None,
        photo_size_bytes=None,
        # 새 ORM 행에서 응답 조립이 lazy load를 시도하지 않도록 명시적으로 고정합니다.
        verified_visit=None,
    )
    session.add(attempt)
    try:
        await session.commit()
    except IntegrityError:
        # 두 오프라인 재시도가 동시에 도착해도 client_capture_id는 한 시도만 만듭니다.
        await session.rollback()
        existing = await territory_repo.get_by_client_capture(
            session, app_user_id, body.client_capture_id
        )
        if existing is None:
            raise
        if not _same_capture(existing, body):
            raise TerritoryAttemptConflictError(
                "capture_id_conflict",
                "같은 client_capture_id에 다른 촬영 증거를 보낼 수 없습니다.",
            ) from None
        return existing, _ticket_for(existing), False
    await session.refresh(attempt)
    return attempt, ticket, True


async def get_attempt(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> TerritoryAttempt:
    attempt = await territory_repo.get_owned(session, app_user_id, attempt_id)
    if attempt is None:
        raise TerritoryAttemptNotFoundError
    return attempt


async def confirm_upload(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> tuple[TerritoryAttempt, bool]:
    """사진 실존을 확인하고 즉시 VISION_PENDING으로 넘깁니다.

    여기서는 사진을 열거나 VLM을 기다리지 않습니다. 두 번째 반환값은 이번 호출이
    새로 상태를 넘겼는지이며, 실제 워커 발행은 VLM PR이 이 경계 뒤에 붙입니다.
    """
    attempt = await territory_repo.get_owned(session, app_user_id, attempt_id, for_update=True)
    if attempt is None:
        raise TerritoryAttemptNotFoundError
    if attempt.status != "PENDING_UPLOAD":
        return attempt, False
    stored = get_storage().stat(attempt.photo_storage_key)
    if stored is None:
        raise TerritoryAttemptConflictError(
            "photo_not_uploaded",
            "업로드된 사진을 찾을 수 없습니다.",
        )
    if stored.size_bytes <= 0 or stored.size_bytes > MAX_TERRITORY_PHOTO_BYTES:
        raise TerritoryAttemptConflictError(
            "invalid_photo_size",
            "사진은 비어 있지 않은 12 MiB 이하 파일이어야 합니다.",
        )
    if stored.content_type is not None and stored.content_type != attempt.photo_content_type:
        raise TerritoryAttemptConflictError(
            "photo_content_type_mismatch",
            "업로드된 객체의 Content-Type이 발급된 사진 형식과 다릅니다.",
        )

    attempt.photo_object_generation = stored.generation
    attempt.photo_size_bytes = stored.size_bytes
    attempt.status = "VISION_PENDING"
    attempt.updated_at = datetime.now(UTC)
    await session.commit()
    return attempt, True


async def record_vision_decision(
    session: AsyncSession,
    attempt_id: uuid.UUID,
    *,
    decision: Literal["verified", "rejected", "failed"],
    model: str,
    model_version: str,
    reason: str | None = None,
) -> TerritoryAttempt:
    """비동기 VLM 결과를 반영하는 유일한 경계.

    판정과 ``VerifiedVisit``을 먼저 commit한 뒤, confirm에서 고정한 generation만
    0바이트 tombstone으로 치환합니다. 저장소 작업이 실패해도 판정은 유실되지 않고 같은
    호출을 재시도하면 정리만 이어집니다.
    """
    if not model.strip() or not model_version.strip():
        raise ValueError("VLM 모델과 버전은 비어 있을 수 없습니다.")

    attempt = await territory_repo.get_for_decision(session, attempt_id)
    if attempt is None:
        raise TerritoryAttemptNotFoundError

    target_status = {
        "verified": "VERIFIED",
        "rejected": "REJECTED",
        "failed": "FAILED",
    }[decision]
    if attempt.status in {"VERIFIED", "REJECTED", "FAILED"}:
        if attempt.status != target_status:
            raise TerritoryAttemptConflictError(
                "vision_decision_conflict",
                "이미 확정된 사진 판정을 다른 결과로 바꿀 수 없습니다.",
            )
        if attempt.photo_redacted_at is None:
            await _redact_decided_photo(session, attempt)
        return attempt
    if attempt.status != "VISION_PENDING":
        raise TerritoryAttemptConflictError(
            "vision_not_ready",
            "사진 업로드 확인 전에는 VLM 결과를 기록할 수 없습니다.",
        )

    now = datetime.now(UTC)
    attempt.status = target_status
    attempt.vision_model = model
    attempt.vision_model_version = model_version
    attempt.decision_reason = reason
    attempt.updated_at = now
    if decision == "verified":
        visit = VerifiedVisit(
            id=uuid.uuid4(),
            attempt_id=attempt.id,
            evidence_version=TERRITORY_EVIDENCE_VERSION,
            verified_at=now,
        )
        attempt.verified_visit = visit
        session.add(visit)

    # 외부 저장소 작업보다 판정 사실을 먼저 내구성 있게 확정합니다.
    await session.commit()
    await _redact_decided_photo(session, attempt)
    return attempt


async def _redact_decided_photo(
    session: AsyncSession,
    attempt: TerritoryAttempt,
) -> None:
    generation = attempt.photo_object_generation
    if not generation:
        raise RuntimeError("confirm된 사진 generation이 없습니다.")
    get_storage().redact(attempt.photo_storage_key, generation=generation)
    now = datetime.now(UTC)
    attempt.photo_redacted_at = now
    attempt.updated_at = now
    await session.commit()
