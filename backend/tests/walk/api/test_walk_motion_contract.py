"""Wire digests and invalid-journal rejection. No database or optional skip path."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from daengs_backend.schemas.walk import WalkPointUpload
from daengs_backend.schemas.walk_motion import MotionManifest, MotionObservation
from daengs_backend.services.walk_motion_contract import (
    MotionConflict,
    chunk_digest,
    evidence_digest,
    manifest_digest,
    validate_manifest,
    validate_observations,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/gps-motion-backup-v1.json"


@pytest.mark.parametrize(
    "method,suffix",
    [
        ("GET", "motion-capabilities"),
        ("GET", "trajectory-capabilities"),
        (
            "GET",
            (
                "00000000-0000-0000-0000-000000000001/trajectory-calculation"
                "?version=walk-trajectory-calculation-v1"
            ),
        ),
        ("GET", "00000000-0000-0000-0000-000000000001/motion-calculation"),
        ("PUT", "00000000-0000-0000-0000-000000000001/motion-backup"),
        ("GET", "00000000-0000-0000-0000-000000000001/motion-backup"),
        ("PUT", "00000000-0000-0000-0000-000000000001/motion-backup/chunks/0"),
        ("GET", "00000000-0000-0000-0000-000000000001/motion-backup/chunks/0"),
        ("POST", "00000000-0000-0000-0000-000000000001/motion-backup/complete"),
    ],
)
def test_registered_routes_require_authentication_before_database(method, suffix, monkeypatch):
    from fastapi.testclient import TestClient

    from daengs_backend.main import app
    from daengs_backend.repositories import walk_motion

    async def unexpected_database(*args):
        raise AssertionError("unauthenticated request reached backup storage")

    monkeypatch.setattr(walk_motion, "available", unexpected_database)
    with TestClient(app) as client:
        response = client.request(method, "/app/walks/" + suffix)
    assert response.status_code == 401


def test_golden_wire_preserves_int64_float_bits_null_and_config_text():
    fixture = json.loads(FIXTURE.read_text())
    manifest = MotionManifest.model_validate(fixture["manifest"])
    points = [MotionObservation.model_validate(p) for p in fixture["points"]]
    raw = [WalkPointUpload.model_validate(p) for p in fixture["raw_points"]]
    validate_manifest(manifest)
    validate_observations(manifest, points, raw)
    assert manifest_digest(manifest) == fixture["manifest_fingerprint"]
    assert chunk_digest(points) == fixture["chunk_fingerprint"]
    assert (
        evidence_digest(manifest_digest(manifest), [chunk_digest(points)])
        == fixture["evidence_fingerprint"]
    )
    assert manifest.epochs[0].started_elapsed_nanos == 9007199254740993
    assert [p.speed_mps_bits for p in points] == ["80000000", "7fc00001", None]
    assert MotionManifest.model_validate_json(manifest.model_dump_json()) == manifest
    assert chunk_digest(points[::-1]) != fixture["chunk_fingerprint"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("first_ingress_seq", 1),
        ("target_ingress_seq", 3),
        ("persisted_count", 2),
        ("ended_elapsed_nanos", 0),
        ("end_kind", "PAUSE"),
        ("drained", False),
    ],
)
def test_bad_completed_epoch_is_not_a_receipt(field, value):
    body = json.loads(FIXTURE.read_text())["manifest"]
    body["epochs"][0][field] = value
    with pytest.raises(MotionConflict):
        validate_manifest(MotionManifest.model_validate(body))


@pytest.mark.parametrize(
    "update",
    [
        {"windowSize": True},
        {"windowSize": 129},
        {"minDistanceM": -1},
        {"minCoordinateSeconds": 0},
        {"windowSeconds": 30},
        {"reentrySamples": 0},
        {"reentrySeconds": 21},
        {"extra": 3},
        {"maxJumpM": 3},
    ],
)
def test_invalid_settings_with_valid_hash_are_rejected(update):
    body = json.loads(FIXTURE.read_text())["manifest"]
    config = json.loads(body["policy"]["config_json"])
    config.update(update)
    text = json.dumps(config)
    body["policy"].update(config_json=text, config_hash=hashlib.sha256(text.encode()).hexdigest())
    with pytest.raises(MotionConflict, match="motion_config_invalid"):
        validate_manifest(MotionManifest.model_validate(body))


def test_unknown_policy_hash_and_missing_or_coerced_metadata_are_not_accepted():
    data = json.loads(FIXTURE.read_text())
    manifest = copy.deepcopy(data["manifest"])
    manifest["policy"]["measurement_version"] = "future"
    with pytest.raises(ValidationError):
        MotionManifest.model_validate(manifest)
    manifest["policy"]["measurement_version"] = "motion-measurement-v1"
    manifest["policy"]["config_hash"] = "0" * 64
    with pytest.raises(MotionConflict, match="motion_config_hash_mismatch"):
        validate_manifest(MotionManifest.model_validate(manifest))
    for key, value in [
        ("client_seq", True),
        ("elapsed_realtime_nanos", 1.5),
        ("recording_eligible", 1),
        ("speed_mps_bits", "NaN"),
    ]:
        point = dict(data["points"][0], **{key: value})
        with pytest.raises(ValidationError):
            MotionObservation.model_validate(point)
    point = data["points"][0]
    del point["speed_mps_bits"]
    with pytest.raises(ValidationError):
        MotionObservation.model_validate(point)


def test_pause_empty_stop_order_and_exact_reception_cutoff():
    data = json.loads(FIXTURE.read_text())
    first = data["manifest"]["epochs"][0]
    first["end_kind"] = "PAUSE"
    last = dict(
        first,
        source_epoch="epoch-b",
        chain_index=1,
        started_elapsed_nanos=first["ended_elapsed_nanos"],
        first_ingress_seq=3,
        persisted_count=0,
        end_kind="STOP",
    )
    data["manifest"]["epochs"].append(last)
    manifest = MotionManifest.model_validate(data["manifest"])
    validate_manifest(manifest)
    points = [MotionObservation.model_validate(p) for p in data["points"]]
    raw = [WalkPointUpload.model_validate(p) for p in data["raw_points"]]
    validate_observations(manifest, points, raw)
    points[-1] = points[-1].model_copy(
        update={"received_elapsed_nanos": first["ended_elapsed_nanos"] + 1}
    )
    with pytest.raises(MotionConflict, match="motion_reception_after_stop"):
        validate_observations(manifest, points, raw)
    for field, value in [
        ("source_epoch", "epoch-a"),
        ("clock_epoch_id", "another"),
        ("chain_index", 0),
    ]:
        body = copy.deepcopy(data["manifest"])
        body["epochs"][1][field] = value
        with pytest.raises(MotionConflict):
            validate_manifest(MotionManifest.model_validate(body))
