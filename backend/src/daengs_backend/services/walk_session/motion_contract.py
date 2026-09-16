"""Canonical backup digests and journal validation, independent of storage and HTTP."""

import hashlib
import json

from daengs_backend.schemas.walk_motion import MotionManifest, MotionObservation


class MotionConflict(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def digest(value) -> str:
    # ASCII only strings in metadata; config_json is hashed separately as exact UTF-8 bytes.
    wire = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(wire.encode("utf-8")).hexdigest()


def manifest_digest(manifest: MotionManifest) -> str:
    policy = manifest.policy
    return digest(
        [
            manifest.version,
            manifest.client_session_id,
            manifest.raw_input_fingerprint,
            manifest.point_count,
            policy.version,
            policy.observation_schema_version,
            policy.measurement_version,
            policy.config_hash,
            [list(epoch.model_dump().values()) for epoch in manifest.epochs],
        ]
    )


def chunk_digest(points: list[MotionObservation]) -> str:
    return digest([list(point.model_dump().values()) for point in points])


def evidence_digest(manifest_fingerprint: str, chunk_fingerprints: list[str]) -> str:
    return digest(["gps-motion-backup-v1", manifest_fingerprint, chunk_fingerprints])


def validate_manifest(manifest: MotionManifest) -> None:
    policy = manifest.policy
    if hashlib.sha256(policy.config_json.encode("utf-8")).hexdigest() != policy.config_hash:
        raise MotionConflict("motion_config_hash_mismatch")
    # Validate the frozen motion-v1 settings without substituting today's defaults.
    try:
        config = json.loads(
            policy.config_json, parse_constant=lambda _: None, object_pairs_hook=_unique_object
        )
        integer = {"windowSize", "reentrySamples"}
        positive = {
            "windowSeconds",
            "minCoordinateSeconds",
            "maxPositionAccuracyM",
            "maxSpeedAccuracyMps",
            "speedAgreementMps",
            "maxWalkingSpeedMps",
            "stationarySpeedMps",
            "minDistanceM",
            "noiseRadiusFactor",
            "maxJumpM",
            "maxGapSeconds",
            "reentrySeconds",
        }
        valid = (
            isinstance(config, dict)
            and config.keys() == integer | positive
            and all(type(v) in (int, float) and 0 <= v < float("inf") for v in config.values())
            and all(type(config[k]) is int for k in integer)
            and 2 <= config["windowSize"] <= 128
            and 1 <= config["reentrySamples"] <= 128
            and 0
            < config["minCoordinateSeconds"]
            <= config["windowSeconds"]
            <= config["maxGapSeconds"]
            <= 3600
            and config["maxWalkingSpeedMps"] > config["stationarySpeedMps"]
            and config["maxJumpM"] > config["minDistanceM"]
            and config["reentrySeconds"] <= config["maxGapSeconds"]
        )
        if not valid:
            raise ValueError("unsupported config")
    except (ValueError, TypeError):
        raise MotionConflict("motion_config_invalid") from None
    expected = 0
    previous = None
    sources = set()
    for index, epoch in enumerate(manifest.epochs):
        if (
            not epoch.drained
            or epoch.source_epoch in sources
            or epoch.first_ingress_seq != expected
            or epoch.target_ingress_seq != expected + epoch.persisted_count - 1
            or epoch.ended_elapsed_nanos < epoch.started_elapsed_nanos
            or epoch.end_kind != ("STOP" if index == len(manifest.epochs) - 1 else "PAUSE")
            or (
                previous is not None
                and (
                    epoch.clock_epoch_id != previous.clock_epoch_id
                    or epoch.chain_index <= previous.chain_index
                    or epoch.started_elapsed_nanos < previous.ended_elapsed_nanos
                )
            )
        ):
            raise MotionConflict("motion_epoch_mismatch")
        sources.add(epoch.source_epoch)
        expected += epoch.persisted_count
        previous = epoch
    if expected != manifest.point_count:
        raise MotionConflict("motion_point_count_mismatch")
    if sum(e.ended_elapsed_nanos - e.started_elapsed_nanos for e in manifest.epochs) > 2**63 - 1:
        raise MotionConflict("motion_duration_overflow")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate config key")
        result[key] = value
    return result


def validate_observations(manifest, observations, raw_points):
    epochs = {e.source_epoch: e for e in manifest.epochs}
    for point, raw in zip(observations, raw_points, strict=True):
        epoch = epochs.get(point.source_epoch)
        if (
            epoch is None
            or point.client_seq != raw.client_seq
            or point.chain_index != raw.chain_index
            or point.chain_index != epoch.chain_index
            or point.clock_epoch_id != epoch.clock_epoch_id
            or not epoch.first_ingress_seq <= point.client_seq <= epoch.target_ingress_seq
            or point.recording_eligible != raw.recording_eligible
        ):
            raise MotionConflict("motion_observation_mismatch")
        # Invalid/missing measurement time remains raw input, not a silently fabricated fix.
        if (
            point.recording_eligible
            and point.received_elapsed_nanos is not None
            and point.received_elapsed_nanos > epoch.ended_elapsed_nanos
        ):
            raise MotionConflict("motion_reception_after_stop")
