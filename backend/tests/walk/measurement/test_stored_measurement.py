"""A shared wire fixture is replayed by the actual Kotlin detail consumer too."""

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from daengs_backend.schemas.walk import WalkPointUpload
from daengs_backend.schemas.walk_measurement import RoutePage
from daengs_backend.schemas.walk_motion import MotionManifest, MotionObservation
from daengs_backend.schemas.walk_precision import PrecisionPoint
from daengs_backend.services.walk_measurement import project
from daengs_backend.services.walk_precision_contract import refine_points

FIXTURES = Path(__file__).parents[1] / "fixtures"
CASES = json.loads((FIXTURES / "gps-motion-precision-v1.json").read_text(encoding="utf-8"))["cases"]
OWNER = UUID("22222222-2222-2222-2222-222222222222")
WALK = UUID("33333333-3333-3333-3333-333333333333")


def projected(case):
    raw = [WalkPointUpload.model_validate(p) for p in case["raw_points"]]
    raw = refine_points([PrecisionPoint.model_validate(p) for p in case["precision_points"]], raw)
    inputs = (
        MotionManifest.model_validate(case["manifest"]),
        raw,
        [MotionObservation.model_validate(p) for p in case["points"]],
        case["evidence_fingerprint"],
        case["precision_fingerprint"],
    )
    return project(inputs, OWNER, WALK)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_sealed_pages_keep_original_sources_and_interval_contributions(case):
    summary, pages = projected(case)
    assert summary.metrics.walking_distance_m == pytest.approx(
        case["expected"]["distance_m"], abs=1e-7, rel=1e-10
    )
    assert summary.motion_recording_duration_ns == case["expected"]["recording_duration_nanos"]
    distance = 0
    for descriptor, payload in zip(summary.required_route_chunks, pages, strict=True):
        assert descriptor.sha256 == hashlib.sha256(payload.encode()).hexdigest()
        assert descriptor.byte_size == len(payload.encode())
        page = RoutePage.model_validate_json(payload)
        assert page.measurement_id == summary.measurement.measurement_id
        assert page.chunk_index == descriptor.index
        assert len(page.points) == descriptor.point_count
        distance += sum(p.walking_distance_m for p in page.points if p.kind == "walking_section")
    assert distance == pytest.approx(summary.metrics.walking_distance_m, abs=1e-7, rel=1e-10)
    fixture = json.loads((FIXTURES / "walk-measurement-v1.json").read_text(encoding="utf-8"))
    stored = next(c for c in fixture["cases"] if c["name"] == case["name"])
    assert stored["summary"] == summary.model_dump_json()
    assert stored["pages"] == pages


def test_long_route_splits_inside_a_section_without_losing_an_incoming_edge():
    case = json.loads(
        (FIXTURES / "walk-measurement-long-input-v1.json").read_text(encoding="utf-8")
    )["cases"][0]
    summary, pages = projected(case)
    expected = json.loads((FIXTURES / "walk-measurement-long-v1.json").read_text(encoding="utf-8"))[
        "cases"
    ][0]
    assert len(pages) > 1
    assert pages == expected["pages"] and summary.model_dump_json() == expected["summary"]
    flat = [p for page in pages for p in RoutePage.model_validate_json(page).points]
    assert flat[256].point_index > 0
    assert sum(p.walking_distance_m for p in flat if p.kind == "walking_section") == pytest.approx(
        summary.metrics.walking_distance_m
    )
