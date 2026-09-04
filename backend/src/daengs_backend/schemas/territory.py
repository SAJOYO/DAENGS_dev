"""산책 중 점령지 촬영·판정 API 계약."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TerritoryAttemptStatus = Literal[
    "PENDING_UPLOAD",
    "VISION_PENDING",
    "VERIFIED",
    "REJECTED",
    "FAILED",
]


class TerritoryAttemptStart(BaseModel):
    """촬영 버튼을 누른 순간 고정되는 위치·사진 메타데이터."""

    model_config = ConfigDict(extra="forbid")

    client_capture_id: uuid.UUID
    client_session_id: uuid.UUID
    site_id: str = Field(
        max_length=96,
        pattern=r"^territory-site:hex-v1:140:-?\d+:-?\d+$",
    )
    captured_at: datetime
    lat: Decimal = Field(ge=-90, le=90, max_digits=9, decimal_places=7)
    lng: Decimal = Field(ge=-180, le=180, max_digits=10, decimal_places=7)
    accuracy_m: float = Field(ge=0, allow_inf_nan=False)
    is_mock: bool = False
    content_type: Literal["image/jpeg", "image/webp"] = "image/jpeg"

    @field_validator("captured_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("촬영 시각에는 timezone이 필요합니다.")
        return value


class TerritoryAttemptResponse(BaseModel):
    attempt_id: uuid.UUID
    client_capture_id: uuid.UUID
    client_session_id: uuid.UUID
    site_id: str
    captured_at: datetime
    status: TerritoryAttemptStatus
    distance_m: float
    accuracy_m: float
    verified_visit_id: uuid.UUID | None
    vision_model: str | None
    vision_model_version: str | None
    decision_reason: str | None
    created_at: datetime
    updated_at: datetime


class TerritoryAttemptTicketResponse(TerritoryAttemptResponse):
    """PENDING_UPLOAD 시도와 사진 직접 업로드 티켓."""

    upload_url: str | None
    upload_headers: dict[str, str]
    expires_in_seconds: int | None
