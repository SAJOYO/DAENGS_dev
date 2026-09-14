"""Cross-runtime reference: expected values were produced by the real Kotlin APP engine."""

import copy
import json
from pathlib import Path

import pytest

from daengs_backend.schemas.walk import WalkPointUpload
from daengs_backend.schemas.walk_motion import MotionManifest, MotionObservation
from daengs_backend.services.walk_metrics.motion_engine import replay
from daengs_backend.services.walk_session.finalize import walk_input_fingerprint
from daengs_backend.services.walk_session.motion_contract import (
    chunk_digest,
    evidence_digest,
    manifest_digest,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/gps-motion-replay-v1.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def assert_same(actual, expected):
    if isinstance(expected, float):
        # JVM/Python transcendental functions need a numerical tolerance, never fuzzy decisions.
        assert actual == pytest.approx(expected, rel=1e-10, abs=1e-7)
    elif isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            assert_same(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for a, e in zip(actual, expected, strict=True):
            assert_same(a, e)
    else:
        assert actual == expected


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_app_journal_decisions_and_totals_agree(case):
    manifest = MotionManifest.model_validate(case["manifest"])
    points = [MotionObservation.model_validate(p) for p in case["points"]]
    raw = [WalkPointUpload.model_validate(p) for p in case["raw_points"]]
    assert walk_input_fingerprint(raw) == manifest.raw_input_fingerprint
    assert manifest_digest(manifest) == case["manifest_fingerprint"]
    assert (
        evidence_digest(
            manifest_digest(manifest),
            [chunk_digest(points[i : i + 256]) for i in range(0, len(points), 256)],
        )
        == case["evidence_fingerprint"]
    )
    steps = []
    result = replay(manifest, points, raw, steps.append)
    assert_same(steps, case["steps"])
    assert_same(result, case["expected"])


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "reordered",
        "extra",
        "wrong_source",
        "incomplete",
        "unknown_policy",
        "config_hash",
    ],
)
def test_invalid_journal_is_not_calculated_with_defaults(change):
    case = copy.deepcopy(next(c for c in CASES if c["name"] == "walking"))
    if change == "missing":
        case["points"].pop()
    elif change == "reordered":
        case["raw_points"].reverse()
    elif change == "extra":
        case["points"].append(case["points"][-1])
    elif change == "wrong_source":
        case["points"][0]["source_epoch"] = "missing"
    elif change == "incomplete":
        case["manifest"]["epochs"][0]["drained"] = False
    elif change == "unknown_policy":
        case["manifest"]["policy"]["version"] = "future"
    else:
        case["manifest"]["policy"]["config_hash"] = "0" * 64
    with pytest.raises(ValueError):
        replay(
            MotionManifest.model_validate(case["manifest"]),
            [MotionObservation.model_validate(p) for p in case["points"]],
            [WalkPointUpload.model_validate(p) for p in case["raw_points"]],
        )


def test_stored_coordinate_precision_is_not_original_device_parity():
    case = next(c for c in CASES if c["name"] == "walking")
    m = MotionManifest.model_validate(case["manifest"])
    points = [MotionObservation.model_validate(p) for p in case["points"]]
    raw = [WalkPointUpload.model_validate(p) for p in case["raw_points"]]
    from decimal import Decimal

    original = [
        p.model_copy(update={"lng": p.lng + Decimal("0.0000004") * (i % 2)})
        for i, p in enumerate(raw)
    ]
    assert walk_input_fingerprint(original) == walk_input_fingerprint(raw)
    assert replay(m, points, original)["distance_m"] != replay(m, points, raw)["distance_m"]
