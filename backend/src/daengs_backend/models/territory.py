"""점령지 방문 인증 원장.

``TerritoryAttempt``는 촬영·위치·VLM 파이프라인의 가변 상태이고,
``VerifiedVisit``는 두 증거가 모두 통과했을 때만 생기는 불변 사실입니다. 실제 점령과
소유권은 이 테이블이 아니라 후속 정책이 ``VerifiedVisit``을 소비해 결정합니다.

스키마 원본은 ``db/init/08_territory_visits.sql``입니다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from daengs_backend.models.base import Base

TERRITORY_ATTEMPT_STATUSES = (
    "PENDING_UPLOAD",
    "VISION_PENDING",
    "VERIFIED",
    "REJECTED",
    "FAILED",
)
TERRITORY_EVIDENCE_VERSION = 1


class TerritoryAttempt(Base):
    """산책 중 한 번의 인앱 촬영과 판정 상태."""

    __tablename__ = "territory_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING_UPLOAD','VISION_PENDING','VERIFIED','REJECTED','FAILED')",
            name="territory_attempts_status_check",
        ),
        CheckConstraint(
            "capture_lat BETWEEN -90 AND 90 AND capture_lng BETWEEN -180 AND 180",
            name="territory_attempts_capture_coordinate_range",
        ),
        CheckConstraint(
            "site_lat BETWEEN -90 AND 90 AND site_lng BETWEEN -180 AND 180",
            name="territory_attempts_site_coordinate_range",
        ),
        CheckConstraint(
            "accuracy_m >= 0 AND distance_m + accuracy_m <= 10",
            name="territory_attempts_location_evidence",
        ),
        CheckConstraint(
            "distance_m >= 0 AND distance_m <= 10",
            name="territory_attempts_distance_range",
        ),
        CheckConstraint("is_mock = FALSE", name="territory_attempts_not_mock"),
        CheckConstraint(
            "photo_content_type IN ('image/jpeg','image/webp')",
            name="territory_attempts_photo_type_check",
        ),
        CheckConstraint(
            "status = 'PENDING_UPLOAD' OR "
            "(photo_object_generation IS NOT NULL "
            "AND btrim(photo_object_generation) <> '' "
            "AND photo_size_bytes > 0 AND photo_size_bytes <= 12582912)",
            name="territory_attempts_confirmed_photo_identity",
        ),
        CheckConstraint(
            "status NOT IN ('VERIFIED','REJECTED','FAILED') "
            "OR (vision_model IS NOT NULL AND btrim(vision_model) <> '' "
            "AND vision_model_version IS NOT NULL AND btrim(vision_model_version) <> '')",
            name="territory_attempts_final_vision_metadata",
        ),
        UniqueConstraint(
            "app_user_id",
            "client_capture_id",
            name="territory_attempts_owner_capture_unique",
        ),
        Index(
            "territory_attempts_owner_created_idx",
            "app_user_id",
            "created_at",
        ),
        Index(
            "territory_attempts_session_idx",
            "app_user_id",
            "client_session_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="CASCADE")
    )

    # 앱 로컬 DB의 재시도 키. capture_id가 같으면 같은 시도입니다.
    client_capture_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    # 촬영 시점에는 서버 Walk 행이 아직 없으므로 서버 walk_id 대신 로컬 세션 id를 둡니다.
    client_session_id: Mapped[uuid.UUID] = mapped_column(Uuid)

    site_id: Mapped[str] = mapped_column(String(96))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capture_lat: Mapped[Decimal] = mapped_column(Numeric(9, 7))
    capture_lng: Mapped[Decimal] = mapped_column(Numeric(10, 7))
    accuracy_m: Mapped[float] = mapped_column(Float)
    is_mock: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))

    # 판정 당시 게임판 좌표를 보존합니다. 나중에 게임판 세대가 바뀌어도 과거 10m 판정은
    # 그때의 좌표로 설명할 수 있어야 합니다.
    site_lat: Mapped[Decimal] = mapped_column(Numeric(9, 7))
    site_lng: Mapped[Decimal] = mapped_column(Numeric(10, 7))
    distance_m: Mapped[float] = mapped_column(Float)

    status: Mapped[str] = mapped_column(String(24), server_default=text("'PENDING_UPLOAD'"))
    photo_storage_key: Mapped[str] = mapped_column(Text, unique=True)
    photo_content_type: Mapped[str] = mapped_column(String(50))
    # confirm에서 고정한 원본 generation과 크기. VLM은 이 generation만 읽어야 합니다.
    photo_object_generation: Mapped[str | None] = mapped_column(Text)
    photo_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    photo_redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vision_model: Mapped[str | None] = mapped_column(Text)
    vision_model_version: Mapped[str | None] = mapped_column(Text)
    decision_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    verified_visit: Mapped[VerifiedVisit | None] = relationship(
        back_populates="attempt",
        cascade="all, delete-orphan",
        uselist=False,
    )


class VerifiedVisit(Base):
    """앱 위치 attestation의 보수적 10m 조건과 강아지 사진 판정을 통과한 사실."""

    __tablename__ = "territory_verified_visits"
    __table_args__ = (
        CheckConstraint(
            "evidence_version > 0",
            name="territory_verified_visits_evidence_version_positive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("territory_attempts.id", ondelete="CASCADE"),
        unique=True,
    )
    evidence_version: Mapped[int] = mapped_column(
        server_default=text(str(TERRITORY_EVIDENCE_VERSION))
    )
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    attempt: Mapped[TerritoryAttempt] = relationship(back_populates="verified_visit")
