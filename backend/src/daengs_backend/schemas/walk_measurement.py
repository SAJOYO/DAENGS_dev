"""Immutable detail transport. Scene bindings are independently versioned later."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from daengs_backend.schemas.walk_trajectory import SourceWallTime
from daengs_walk.trajectory import Contract, LedgerMetrics, SourceRef
from daengs_walk.trajectory_projection import TrajectoryBoundaries
from daengs_walk.trajectory_view import Digest, MeasurementRef

VERSION = "walk-measurement-v1"
CHUNK_SIZE = 256
MAX_POINTS = 100_000
MeasurementVersion = Literal["walk-measurement-v1"]


class RoutePoint(Contract):
    kind: Literal["walking_section", "observed_run"]
    section_id: str
    section_index: int = Field(ge=0)
    point_index: int = Field(ge=0)
    ref: SourceRef
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    wall_time_millis: int
    elapsed_ns: int = Field(ge=0)
    # The incoming final interval owns this contribution; a section starts at zero.
    walking_distance_m: float = Field(ge=0)


class RoutePage(Contract):
    version: MeasurementVersion = VERSION
    measurement_id: str
    chunk_index: int = Field(ge=0)
    points: tuple[RoutePoint, ...] = Field(min_length=1, max_length=CHUNK_SIZE)


class ChunkRef(Contract):
    index: int = Field(ge=0)
    sha256: Digest
    byte_size: int = Field(gt=0)
    point_count: int = Field(ge=1, le=CHUNK_SIZE)


class MeasurementSummary(Contract):
    version: MeasurementVersion = VERSION
    walk_id: UUID
    measurement: MeasurementRef
    base_evidence_fingerprint: str
    precision_fingerprint: str
    observation_policy_status: Literal["experimental"] = "experimental"
    device_result_verified: Literal[False] = False
    metrics: LedgerMetrics
    motion_recording_duration_ns: int = Field(ge=0)
    boundaries: TrajectoryBoundaries
    boundary_wall_times: tuple[SourceWallTime, ...]
    walking_section_count: int = Field(ge=0)
    observed_run_count: int = Field(ge=0)
    route_point_count: int = Field(ge=0)
    required_route_chunks: tuple[ChunkRef, ...]
