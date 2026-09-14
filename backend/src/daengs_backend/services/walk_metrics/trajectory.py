"""Build candidate query bytes off the event loop from a shared measurement projection."""

import asyncio

from daengs_backend.schemas.walk_trajectory import MAX_POINTS, TrajectoryCalculation
from daengs_backend.services.walk_metrics.measurement_projection import project
from daengs_backend.services.walk_session import motion as walk_motion
from daengs_backend.services.walk_session.motion_contract import MotionConflict


def _project(manifest, raw, observations, fingerprint, precision_fp, *, owner, walk_id, expected):
    result = project(
        manifest, raw, observations, fingerprint, precision_fp, owner=owner, expected=expected
    )
    metrics = result.metrics
    response = TrajectoryCalculation(
        walk_id=walk_id,
        measurement=result.measurement,
        result_digest=result.measurement.ref().result_digest,
        observation_policy=result.observation_policy.model_dump(),
        wall_times=result.wall_times,
        locations=result.locations,
        observed_runs=result.observed_runs,
        walking_sections=result.walking_sections,
        boundaries=result.boundaries,
        metrics=metrics,
        average_walking_speed_mps=metrics.average_walking_speed_mps,
        located_fraction_of_known_time=metrics.located_fraction_of_known_time,
        motion_recording_duration_ns=result.motion_recording_duration_ns,
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
