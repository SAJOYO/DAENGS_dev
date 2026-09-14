"""Compute only from the owner's complete immutable backup; never rewrite legacy analysis."""

import asyncio

from daengs_backend.schemas.walk_motion import MotionCalculation
from daengs_backend.services.walk_motion_engine import replay
from daengs_backend.services.walk_session import motion as walk_motion
from daengs_backend.services.walk_session.motion_contract import MotionConflict, manifest_digest


async def calculate(session, owner, walk_id):
    try:
        manifest, raw, observations, fingerprint, precision_fp = await walk_motion.completed_input(
            session, owner, walk_id
        )
        result = await asyncio.to_thread(replay, manifest, observations, raw)
        return MotionCalculation(
            walk_id=walk_id,
            client_session_id=manifest.client_session_id,
            policy_version=manifest.policy.version,
            measurement_version=manifest.policy.measurement_version,
            config_hash=manifest.policy.config_hash,
            manifest_fingerprint=manifest_digest(manifest),
            evidence_fingerprint=fingerprint,
            precision_fingerprint=precision_fp,
            coordinate_basis="device-fix-bits-v1" if precision_fp else "stored-raw-v1-six-decimals",
            **result,
        )
    except MotionConflict:
        raise
    except (ValueError, TypeError, KeyError, OverflowError):
        # Corrupt stored shapes/unknown policies are not a successful legacy calculation.
        raise MotionConflict("motion_calculation_invalid_input") from None
