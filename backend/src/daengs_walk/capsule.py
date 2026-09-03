"""한 WalkAnalysis가 공간 기억의 원판으로 준비됐음을 선언하는 순수 Capsule 계약.

DB·HTTP·외부 provider를 모른다. Walk가 이미 소유한 측정 결과와 산책 당시 확보한
환경 원자만 봉인한다. Place·Journey 보강은 D-046의 후속 경계다.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from daengs_walk.contracts import WalkEvidencePoint

CAPSULE_VERSION = 1
CONTEXT_SNAPSHOT_VERSION = 2
ContextSnapshotVersion = Literal[1, 2]
PrecipitationKind = Literal["none", "rain", "snow", "mixed"]


class FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _timezone_required(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("capsule timestamps must include a timezone")
    return value


class ContextStatus(StrEnum):
    CAPTURED = "captured"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    FAILED = "failed"


class ObservationCapability(FrozenContract):
    """관측 행의 개수와 별개로 해당 세대가 다시 읽을 수 있는 현상."""

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    generation: int = Field(ge=1)


class TrailContextSnapshot(FrozenContract):
    """판단어로 접기 전, 산책 당시 확보한 환경 원자와 그 출처 상태."""

    context_version: ContextSnapshotVersion = CONTEXT_SNAPSHOT_VERSION
    walk_id: uuid.UUID
    status: ContextStatus
    walked_at: datetime
    source_observed_at: datetime | None = None
    captured_at: datetime
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    weather_code: int | None = Field(default=None, ge=0, le=99)
    is_day: bool | None = None
    temperature_c: float | None = Field(default=None, ge=-100, le=100)
    precipitation_kind: PrecipitationKind | None = None
    precipitation_mm: float | None = Field(default=None, ge=0)
    humidity_pct: float | None = Field(default=None, ge=0, le=100)
    sun_elevation_deg: float | None = Field(default=None, ge=-90, le=90)
    failure_reason: str | None = Field(default=None, min_length=1, max_length=256)

    _tz = field_validator("walked_at", "captured_at")(_timezone_required)
    _optional_tz = field_validator("source_observed_at")(
        lambda value: value if value is None else _timezone_required(value)
    )

    @model_validator(mode="after")
    def status_matches_payload(self) -> Self:
        atoms = (
            self.weather_code,
            self.is_day,
            self.temperature_c,
            self.precipitation_kind,
            self.precipitation_mm,
            self.humidity_pct,
            self.sun_elevation_deg,
        )
        observed = any(value is not None for value in atoms)
        if self.status in {ContextStatus.CAPTURED, ContextStatus.PARTIAL} and (
            self.provider is None or not observed
        ):
            raise ValueError("captured or partial context requires a provider and a value")
        if self.status in {ContextStatus.UNKNOWN, ContextStatus.FAILED} and observed:
            raise ValueError("unknown or failed context cannot carry observed values")
        if self.status is ContextStatus.FAILED and self.failure_reason is None:
            raise ValueError("failed context requires a failure reason")
        if self.status is not ContextStatus.FAILED and self.failure_reason is not None:
            raise ValueError("only failed context can carry a failure reason")
        if self.context_version == 1 and self.precipitation_kind is not None:
            raise ValueError("context v1 cannot carry precipitation_kind")
        return self


class WalkCapsuleManifest(FrozenContract):
    """기존 Analysis 자식을 복제하지 않고 완전한 봉인을 선언하는 manifest."""

    capsule_version: Literal[CAPSULE_VERSION] = CAPSULE_VERSION
    walk_id: uuid.UUID
    facts_record_version: int = Field(ge=1)
    calculation_version: int = Field(ge=1)
    receipt_version: int = Field(ge=1)
    observation_version: int = Field(ge=1)
    capabilities: tuple[ObservationCapability, ...] = Field(min_length=1)
    sealed_at: datetime

    _tz = field_validator("sealed_at")(_timezone_required)

    @model_validator(mode="after")
    def capabilities_are_unique(self) -> Self:
        keys = [(item.name, item.generation) for item in self.capabilities]
        if len(keys) != len(set(keys)):
            raise ValueError("capsule capabilities must be unique")
        return self


@dataclass(frozen=True)
class WalkCapsuleArtifacts:
    manifest: WalkCapsuleManifest
    trail_context: TrailContextSnapshot

    def __post_init__(self) -> None:
        if self.manifest.walk_id != self.trail_context.walk_id:
            raise ValueError("capsule manifest and context must belong to one walk")
        if self.manifest.sealed_at < self.trail_context.captured_at:
            raise ValueError("capsule cannot be sealed before context capture")


def build_walk_capsule(
    *,
    walk_id: uuid.UUID,
    facts_record_version: int,
    calculation_version: int,
    receipt_version: int,
    observation_version: int,
    walked_at: datetime,
    sealed_at: datetime,
    weather_code: int | None,
    is_day: bool | None,
    temperature_c: float | None,
    precipitation_kind: PrecipitationKind | None = None,
    precipitation_mm: float | None = None,
    humidity_pct: float | None = None,
    source_observed_at: datetime | None = None,
    failure_reason: str | None = None,
    provider: str = "android_walk_upload_v1",
    context_version: ContextSnapshotVersion = CONTEXT_SNAPSHOT_VERSION,
) -> WalkCapsuleArtifacts:
    """기존 Walk 날씨를 거짓 보충 없이 context로 옮기고 capability를 선언한다."""

    has_context = any(
        value is not None
        for value in (
            weather_code,
            is_day,
            temperature_c,
            precipitation_kind,
            precipitation_mm,
            humidity_pct,
        )
    )
    context = TrailContextSnapshot(
        context_version=context_version,
        walk_id=walk_id,
        status=(
            ContextStatus.PARTIAL
            if has_context
            else ContextStatus.FAILED
            if failure_reason is not None
            else ContextStatus.UNKNOWN
        ),
        walked_at=walked_at,
        source_observed_at=source_observed_at,
        captured_at=sealed_at,
        provider=(
            provider
            if has_context or source_observed_at is not None or failure_reason is not None
            else None
        ),
        weather_code=weather_code,
        is_day=is_day,
        temperature_c=temperature_c,
        precipitation_kind=precipitation_kind,
        precipitation_mm=precipitation_mm,
        humidity_pct=humidity_pct,
        failure_reason=failure_reason,
    )
    capabilities = (
        ObservationCapability(name="low_motion", generation=observation_version),
        ObservationCapability(name="gap", generation=observation_version),
    )
    manifest = WalkCapsuleManifest(
        walk_id=walk_id,
        facts_record_version=facts_record_version,
        calculation_version=calculation_version,
        receipt_version=receipt_version,
        observation_version=observation_version,
        capabilities=capabilities,
        sealed_at=sealed_at,
    )
    return WalkCapsuleArtifacts(manifest=manifest, trail_context=context)


def select_context_anchor(
    points: Sequence[WalkEvidencePoint],
    *,
    started_at: datetime,
    ended_at: datetime,
) -> WalkEvidencePoint | None:
    """시간상 산책 중앙에 가장 가까운 실제 기기 좌표를 고른다."""

    _timezone_required(started_at)
    _timezone_required(ended_at)
    if ended_at < started_at:
        raise ValueError("walk end cannot precede start")
    midpoint = started_at + (ended_at - started_at) / 2
    candidates = (point for point in points if not point.is_mock)
    return min(
        candidates,
        key=lambda point: (abs((point.at - midpoint).total_seconds()), point.client_seq),
        default=None,
    )
