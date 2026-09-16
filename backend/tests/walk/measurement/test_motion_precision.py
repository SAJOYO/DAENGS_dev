"""Shared precision vectors also run through the actual Kotlin engine in APP."""

import json
import struct
from pathlib import Path

import pytest

from daengs_backend.schemas.walk import WalkPointUpload
from daengs_backend.schemas.walk_motion import MotionManifest, MotionObservation
from daengs_backend.schemas.walk_precision import PrecisionManifest, PrecisionPoint
from daengs_backend.services.walk_metrics.motion_engine import replay
from daengs_backend.services.walk_session.motion_contract import MotionConflict
from daengs_backend.services.walk_session.precision_contract import (
    chunk_digest,
    evidence_digest,
    manifest_digest,
    refine_points,
)

CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures/gps-motion-precision-v1.json").read_text(
        encoding="utf-8"
    )
)["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_precision_vectors(case):
    raw = [WalkPointUpload.model_validate(p) for p in case["raw_points"]]
    points = [PrecisionPoint.model_validate(p) for p in case["precision_points"]]
    manifest = PrecisionManifest.model_validate(case["precision_manifest"])
    md = manifest_digest(manifest)
    assert md == case["precision_manifest_fingerprint"]
    assert (
        evidence_digest(md, [chunk_digest(points[i : i + 256]) for i in range(0, len(points), 256)])
        == case["precision_fingerprint"]
    )
    precise = refine_points(points, raw)
    for p, r in zip(points, precise, strict=True):
        assert struct.pack("!d", r.lat).hex() == p.lat_bits
        assert struct.pack("!d", r.lng).hex() == p.lng_bits
    result = replay(
        MotionManifest.model_validate(case["manifest"]),
        [MotionObservation.model_validate(p) for p in case["points"]],
        precise,
    )
    expected = case["expected"]
    assert result.pop("distance_m") == pytest.approx(expected["distance_m"], rel=1e-10, abs=1e-7)
    assert result == {k: v for k, v in expected.items() if k != "distance_m"}


@pytest.mark.parametrize(
    "change",
    [
        {"lat_bits": "7ff0000000000000"},
        {"lng_bits": "7ff8000000000001"},
        {"lat_bits": struct.pack("!d", 91).hex()},
        {"lng_bits": struct.pack("!d", 10).hex()},
        {"accuracy_bits": None},
        {"accuracy_bits": "7f800000"},
        {"accuracy_bits": "bf800000"},
        {"client_seq": 4},
    ],
)
def test_precision_rejects_nonfinite_wrong_raw_and_wrong_refs(change):
    raw = WalkPointUpload(
        client_seq=0, chain_index=0, lat=37, lng=127, at="2026-09-11T00:00:00Z", accuracy_m=5
    )
    p = PrecisionPoint(
        client_seq=0,
        lat_bits=struct.pack("!d", 37).hex(),
        lng_bits=struct.pack("!d", 127).hex(),
        accuracy_bits="40a00000",
    )
    with pytest.raises(MotionConflict):
        refine_points([p.model_copy(update=change)], [raw])


def test_signed_zero_preserved_after_coarse_cross_check():
    raw = WalkPointUpload(
        client_seq=0, chain_index=0, lat=0, lng=0, at="2026-09-11T00:00:00Z", accuracy_m=0
    )
    p = PrecisionPoint(
        client_seq=0,
        lat_bits="8000000000000000",
        lng_bits="0000000000000000",
        accuracy_bits="80000000",
    )
    actual = refine_points([p], [raw])[0]
    assert struct.pack("!d", actual.lat).hex() == p.lat_bits
    assert struct.pack("!f", actual.accuracy_m).hex() == p.accuracy_bits
