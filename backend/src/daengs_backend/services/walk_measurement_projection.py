"""Validated, detached motion evidence projected once for query and storage consumers.

This module owns calculation, final-ledger projection and original source clocks.
It does not build a query response, serialize JSON or publish a measurement.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from daengs_backend.services.walk_motion_contract import MotionConflict
from daengs_backend.services.walk_trajectory_shadow import (
    ObservedConnectionPolicy,
    backup_fingerprint,
    replay_shadow,
)
from daengs_walk.trajectory import LedgerMetrics
from daengs_walk.trajectory_projection import (
    PathSection,
    SourceWallTime,
    TrajectoryBoundaries,
    TrajectoryLocation,
)
from daengs_walk.trajectory_view import MeasurementSnapshot


@dataclass(frozen=True)
class MeasurementProjection:
    measurement: MeasurementSnapshot
    observation_policy: ObservedConnectionPolicy
    wall_times: tuple[SourceWallTime, ...]
    locations: tuple[TrajectoryLocation, ...]
    observed_runs: tuple[PathSection, ...]
    walking_sections: tuple[PathSection, ...]
    boundaries: TrajectoryBoundaries
    metrics: LedgerMetrics
    motion_recording_duration_ns: int


def project(
    manifest, raw, observations, fingerprint, precision_fp, *, owner, expected=None
) -> MeasurementProjection:
    if backup_fingerprint(manifest, observations) != fingerprint:
        raise MotionConflict("trajectory_evidence_fingerprint")
    result = replay_shadow(
        manifest, observations, raw, owner_id=str(owner), precision_fingerprint=precision_fp
    )
    if expected is not None and result.snapshot.measurement_id != expected:
        raise MotionConflict("trajectory_measurement_changed")
    # Superseded decisions explain the calculation; they cannot be summed or drawn.
    ledger = result.snapshot.ledger.model_copy(update={"superseded": ()})
    snapshot = result.snapshot.model_copy(update={"ledger": ledger})
    epochs = {(e.source_epoch, e.clock_epoch_id): e for e in manifest.epochs}
    wall_times = []
    for event in ledger.journal.events:
        ref = event.ref
        if ref.ingress_seq is not None:
            elapsed = raw[ref.ingress_seq].at - datetime(1970, 1, 1, tzinfo=UTC)
            millis = (
                elapsed.days * 86_400_000 + elapsed.seconds * 1000 + elapsed.microseconds // 1000
            )
        else:
            epoch = epochs[ref.source_epoch, ref.clock_epoch_id]
            millis = (
                epoch.started_at_millis
                if ref.control_kind == "epoch_start"
                else epoch.ended_at_millis
            )
        wall_times.append(SourceWallTime(ref=ref, original_wall_time_millis=millis))
    return MeasurementProjection(
        measurement=snapshot,
        observation_policy=result.observation_policy,
        wall_times=tuple(wall_times),
        locations=result.locations,
        observed_runs=result.observed_runs,
        walking_sections=result.walking_sections,
        boundaries=result.boundaries,
        metrics=ledger.metrics(),
        motion_recording_duration_ns=result.motion_active_duration_ns,
    )
