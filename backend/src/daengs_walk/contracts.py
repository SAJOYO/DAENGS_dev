"""산책 측정 커널이 주고받는 불변 값.

HTTP 요청·응답 DTO도, SQLAlchemy 영속 모델도 아니다. 원본 좌표를 계산 가능한 한
가지 모양으로 정규화하고 그 결과를 다음 Cellophane·Capsule 단계에 넘기는 서비스
내부 계약이다. 반려견 귀속은 ``walk_pets``의 책임이라 이 값들에는 복제하지 않는다.
"""

import math
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WALK_FACTS_RECORD_VERSION = 1
# Geo 계산 정책 v4를 같은 문턱값과 경계 규칙으로 옮긴다.
WALK_CALCULATION_VERSION = 4
MICRO_OBSERVATION_VERSION = 1
MEASUREMENT_RECEIPT_VERSION = 1

EvidenceOrigin = Literal["device", "mock", "mixed", "unknown"]


def _timezone_required(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("walk timestamps must include a timezone")
    return value


class FrozenContract(BaseModel):
    """조용한 필드 유실과 계산 뒤 변형을 모두 막는다."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class WalkEvidencePoint(FrozenContract):
    """저장 형식과 무관하게 계산층이 받는 원본 좌표 하나."""

    client_seq: int = Field(ge=0)
    chain_index: int = Field(default=0, ge=0)
    at: datetime
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)
    is_mock: bool = False

    _timestamp_has_timezone = field_validator("at")(_timezone_required)


class FixQualityCounts(FrozenContract):
    """원본 점이 canonical 구간이 되기까지 생긴 수와 단절."""

    received: int = Field(ge=0)
    accepted: int = Field(ge=0)
    rejected_low_accuracy: int = Field(ge=0)
    rejected_out_of_order: int = Field(ge=0)
    rejected_before_start: int = Field(ge=0)
    rejected_after_end: int = Field(ge=0)
    unknown_accuracy: int = Field(ge=0)
    jump_breaks: int = Field(ge=0)
    gap_breaks: int = Field(ge=0)
    explicit_breaks: int = Field(ge=0)
    dropped_at_capacity: int = Field(ge=0)
    mock_fixes: int = Field(ge=0)

    @model_validator(mode="after")
    def counts_fit_received_points(self) -> Self:
        if self.accepted > self.received:
            raise ValueError("accepted fixes cannot exceed received fixes")
        if self.unknown_accuracy > self.accepted:
            raise ValueError("unknown accuracy cannot exceed accepted fixes")
        if self.mock_fixes > self.received:
            raise ValueError("mock fixes cannot exceed received fixes")
        return self


class CanonicalWalkFacts(FrozenContract):
    """해석이나 점수 없이 산책 한 번에서 확정한 측정 사실."""

    record_version: Literal[WALK_FACTS_RECORD_VERSION] = WALK_FACTS_RECORD_VERSION
    calculation_version: Literal[WALK_CALCULATION_VERSION] = WALK_CALCULATION_VERSION
    walk_id: uuid.UUID
    evidence_origin: EvidenceOrigin
    started_at: datetime
    ended_at: datetime
    duration_s: int = Field(ge=0)
    distance_m: int = Field(ge=0)
    moving_distance_m: int = Field(ge=0)
    moving_s: int = Field(ge=0)
    stop_count: int = Field(ge=0)
    stop_s: int = Field(ge=0)
    avg_speed_mps: float | None = Field(default=None, ge=0)
    fix_count: int = Field(ge=0)

    _timestamps_have_timezones = field_validator("started_at", "ended_at")(_timezone_required)

    @model_validator(mode="after")
    def facts_are_consistent(self) -> Self:
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        if self.moving_distance_m > self.distance_m:
            raise ValueError("moving distance cannot exceed total distance")
        if self.moving_s + self.stop_s > self.duration_s:
            raise ValueError("moving and stop time cannot exceed canonical duration")
        wall_s = round((self.ended_at - self.started_at).total_seconds())
        if self.duration_s > wall_s:
            raise ValueError("canonical duration cannot exceed session wall time")
        return self


class MotionEventOccurrence(FrozenContract):
    """정지로 관측된 한 구간. 이유나 장소 의미는 붙이지 않는다."""

    walk_id: uuid.UUID
    event_index: int = Field(ge=0)
    kind: Literal["stop"] = "stop"
    started_at: datetime
    ended_at: datetime
    duration_s: int = Field(ge=0)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    route_offset_m: float = Field(ge=0)
    accuracy_p50_m: float | None = Field(default=None, ge=0)
    fix_count: int = Field(ge=2)

    _timestamps_have_timezones = field_validator("started_at", "ended_at")(_timezone_required)

    @model_validator(mode="after")
    def ends_after_start(self) -> Self:
        if self.ended_at < self.started_at:
            raise ValueError("event end cannot precede its start")
        return self


class MicroObservation(FrozenContract):
    """후보 저속 구간 또는 관측 공백. 아직 행동 판정은 아니다."""

    generation: Literal[MICRO_OBSERVATION_VERSION] = MICRO_OBSERVATION_VERSION
    walk_id: uuid.UUID
    index: int = Field(ge=0)
    kind: Literal["slow", "gap"]
    started_at: datetime
    ended_at: datetime
    duration_s: float = Field(ge=0)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    path_m: float = Field(ge=0)
    net_m: float = Field(ge=0)
    span_m: float = Field(ge=0)
    fix_count: int = Field(ge=2)
    accuracy_p50_m: float | None = Field(default=None, ge=0)
    route_offset_m: float = Field(ge=0)
    chain_index: int = Field(ge=0)
    abuts_break: bool

    _timestamps_have_timezones = field_validator("started_at", "ended_at")(_timezone_required)

    @model_validator(mode="after")
    def observation_is_consistent(self) -> Self:
        if self.ended_at < self.started_at:
            raise ValueError("observation end cannot precede its start")
        if self.kind == "gap" and (self.path_m != 0 or self.span_m != 0):
            raise ValueError("an unobserved gap cannot claim an observed path or span")
        return self


class MovingSpeedProfile(FrozenContract):
    """이동 구간 속도 분포. 기준 속도를 하나로 굽지 않고 남긴다."""

    p50: float = Field(ge=0)
    p70: float = Field(ge=0)
    p80: float = Field(ge=0)
    p90: float = Field(ge=0)
    sample_n: int = Field(ge=1)

    @model_validator(mode="after")
    def percentiles_are_ordered(self) -> Self:
        if not self.p50 <= self.p70 <= self.p80 <= self.p90:
            raise ValueError("speed percentiles must be ordered")
        return self


class DriftAssessment(StrEnum):
    NOT_ASSESSED = "not_assessed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SUSPECTED = "suspected"


class MeasurementReceipt(FrozenContract):
    """신뢰도 점수 대신 이름 붙은 원시 분자·분모를 고정한다."""

    receipt_version: Literal[MEASUREMENT_RECEIPT_VERSION] = MEASUREMENT_RECEIPT_VERSION
    walk_id: uuid.UUID
    evidence_origin: EvidenceOrigin
    received_fix_count: int = Field(ge=0)
    accepted_fix_count: int = Field(ge=0)
    rejected_low_accuracy_count: int = Field(ge=0)
    rejected_out_of_order_count: int = Field(ge=0)
    rejected_before_start_count: int = Field(ge=0)
    rejected_after_end_count: int = Field(ge=0)
    unknown_accuracy_count: int = Field(ge=0)
    jump_break_count: int = Field(ge=0)
    gap_break_count: int = Field(ge=0)
    explicit_break_count: int = Field(ge=0)
    dropped_at_capacity_count: int = Field(ge=0)
    mock_fix_count: int = Field(ge=0)
    session_wall_time_s: float = Field(ge=0)
    canonical_segment_time_s: float = Field(ge=0)
    gap_elapsed_s: float = Field(ge=0)
    reported_accuracy_count: int = Field(ge=0)
    reported_accuracy_p50_m: float | None = Field(default=None, ge=0)
    reported_accuracy_p90_m: float | None = Field(default=None, ge=0)
    accepted_accuracy_count: int = Field(ge=0)
    accepted_accuracy_p50_m: float | None = Field(default=None, ge=0)
    accepted_accuracy_p90_m: float | None = Field(default=None, ge=0)
    drift_assessment: DriftAssessment = DriftAssessment.NOT_ASSESSED
    drift_assessment_method: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def receipt_is_consistent(self) -> Self:
        if self.accepted_fix_count > self.received_fix_count:
            raise ValueError("accepted fixes cannot exceed received fixes")
        if self.unknown_accuracy_count > self.accepted_fix_count:
            raise ValueError("unknown accuracy cannot exceed accepted fixes")
        if self.mock_fix_count > self.received_fix_count:
            raise ValueError("mock fixes cannot exceed received fixes")
        if self.reported_accuracy_count > self.received_fix_count:
            raise ValueError("reported accuracy cannot exceed received fixes")
        if self.accepted_accuracy_count > self.accepted_fix_count:
            raise ValueError("accepted accuracy cannot exceed accepted fixes")
        if self.canonical_segment_time_s > self.session_wall_time_s + 1e-6:
            raise ValueError("canonical time cannot exceed session wall time")
        if self.gap_elapsed_s > self.session_wall_time_s + 1e-6:
            raise ValueError("gap time cannot exceed session wall time")
        self._check_percentiles(
            self.reported_accuracy_count,
            self.reported_accuracy_p50_m,
            self.reported_accuracy_p90_m,
            "reported",
        )
        self._check_percentiles(
            self.accepted_accuracy_count,
            self.accepted_accuracy_p50_m,
            self.accepted_accuracy_p90_m,
            "accepted",
        )
        if self.drift_assessment is DriftAssessment.NOT_ASSESSED:
            if self.drift_assessment_method is not None:
                raise ValueError("unassessed drift cannot name a method")
        elif self.drift_assessment_method is None:
            raise ValueError("assessed drift requires a method")
        return self

    @staticmethod
    def _check_percentiles(
        count: int,
        p50: float | None,
        p90: float | None,
        label: str,
    ) -> None:
        if count == 0 and (p50 is not None or p90 is not None):
            raise ValueError(f"{label} percentiles require a positive count")
        if count > 0 and (p50 is None or p90 is None):
            raise ValueError(f"{label} accuracy count requires p50 and p90")
        if (
            p50 is not None
            and p90 is not None
            and (not math.isfinite(p50) or not math.isfinite(p90) or p50 > p90)
        ):
            raise ValueError(f"{label} accuracy percentiles are invalid")
