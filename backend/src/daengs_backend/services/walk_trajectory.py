"""Detach validated evidence, then compute/serialize one candidate off the DB lock."""

import asyncio
from datetime import UTC, datetime

from daengs_backend.schemas.walk_trajectory import (
    MAX_POINTS,
    SourceWallTime,
    TrajectoryCalculation,
)
from daengs_backend.services import walk_motion
from daengs_backend.services.walk_motion_contract import MotionConflict
from daengs_backend.services.walk_trajectory_shadow import backup_fingerprint, replay_shadow


def _project(manifest, raw, observations, fingerprint, precision_fp, *, owner, walk_id, expected):
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
    metrics = ledger.metrics()
    response = TrajectoryCalculation(
        walk_id=walk_id,
        measurement=snapshot,
        result_digest=snapshot.ref().result_digest,
        observation_policy=result.observation_policy.model_dump(),
        wall_times=tuple(wall_times),
        locations=result.locations,
        observed_runs=result.observed_runs,
        walking_sections=result.walking_sections,
        boundaries=result.boundaries,
        metrics=metrics,
        average_walking_speed_mps=metrics.average_walking_speed_mps,
        located_fraction_of_known_time=metrics.located_fraction_of_known_time,
        motion_recording_duration_ns=result.motion_active_duration_ns,
    )
    # Serialization also belongs off the event loop; these exact bytes are hashed by HTTP.
    return response.model_dump_json().encode("utf-8")


async def calculate(session, owner, walk_id, *, expected_measurement_id=None) -> bytes:
    try:
        inputs = await walk_motion.completed_input(session, owner, walk_id)
        if inputs[0].point_count > MAX_POINTS:
            raise MotionConflict("trajectory_point_limit")
        return await asyncio.to_thread(
            _project,
            *inputs,
            owner=owner,
            walk_id=walk_id,
            expected=expected_measurement_id,
        )
    except MotionConflict:
        raise
    except (ValueError, TypeError, KeyError, OverflowError):
        raise MotionConflict("trajectory_calculation_invalid_input") from None
