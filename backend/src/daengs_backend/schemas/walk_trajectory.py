"""Opt-in, whole-result candidate transport; not an active/persisted read view."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from daengs_walk.trajectory import Contract, LedgerMetrics
from daengs_walk.trajectory_projection import (
    PathSection,
    SourceWallTime,
    TrajectoryBoundaries,
    TrajectoryLocation,
)
from daengs_walk.trajectory_view import Digest, MeasurementSnapshot

VERSION = "walk-trajectory-calculation-v1"
# A bounded diagnostic response until immutable storage/chunk delivery is available.
MAX_POINTS = 10_000
TrajectoryVersion = Literal["walk-trajectory-calculation-v1"]
MeasurementId = Annotated[str, Field(pattern=r"^shadow-[0-9a-f]{64}$")]


class ObservationPolicyInfo(Contract):
    version: Literal["motion-shadow-observed-v1"]
    max_gap_seconds: float = Field(gt=0)
    max_edge_m: float = Field(gt=0)


class TrajectoryCalculation(Contract):
    version: TrajectoryVersion = VERSION
    walk_id: UUID
    status: Literal["candidate"] = "candidate"
    device_result_verified: Literal[False] = False
    observation_policy_status: Literal["experimental"] = "experimental"
    measurement: MeasurementSnapshot
    result_digest: Digest
    observation_policy: ObservationPolicyInfo
    wall_times: tuple[SourceWallTime, ...]
    locations: tuple[TrajectoryLocation, ...]
    observed_runs: tuple[PathSection, ...]
    walking_sections: tuple[PathSection, ...]
    boundaries: TrajectoryBoundaries
    metrics: LedgerMetrics
    average_walking_speed_mps: float | None = Field(ge=0)
    located_fraction_of_known_time: float | None = Field(ge=0, le=1)
    # Legacy motion-v1 recording duration is distinct from located/moving duration.
    motion_recording_duration_ns: int = Field(ge=0)
