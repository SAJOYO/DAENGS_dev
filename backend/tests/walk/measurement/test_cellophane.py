import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from daengs_walk import analyze_walk, build_cellophane
from daengs_walk.cellophane import (
    CANONICAL_PAINT_SPEC,
    CANONICAL_RADIUS_U,
    CANONICAL_SAMPLE_STEP_M,
    NARROW_STEP,
    BrushProfile,
    Cellophane,
    brush_stamp,
    paint_sheet,
    paint_spec,
)
from daengs_walk.hex_grid import GRID_VERSION, cell_size_m, hex_cell, hex_center_latlng

EARTH_R = 6_371_000.0
LAT = 37.5
LNG = 127.0


def _bundle(walk_id, started_at, point, *, chain_break: bool = False):
    points = [
        point(second, float(second), seq=index) for index, second in enumerate(range(0, 61, 10))
    ]
    if chain_break:
        points += [
            point(70 + second, 240.0 + second, seq=len(points) + index, chain_index=1)
            for index, second in enumerate(range(0, 41, 10))
        ]
    return analyze_walk(walk_id, started_at, started_at + timedelta(seconds=120), points)


def _ground_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlat = lat2 - lat1
    dlng = math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(h))


def test_canonical_product_spec_is_fixed() -> None:
    assert CANONICAL_PAINT_SPEC.paint_version == 2
    assert CANONICAL_PAINT_SPEC.grid_version == GRID_VERSION == "hex-v1"
    assert CANONICAL_PAINT_SPEC.radius_u == CANONICAL_RADIUS_U == 8.0
    assert CANONICAL_PAINT_SPEC.sample_step_m == CANONICAL_SAMPLE_STEP_M == 1.5
    assert CANONICAL_PAINT_SPEC.profile_fp == NARROW_STEP.fingerprint


def test_build_cellophane_preserves_canonical_time(walk_id, started_at, point) -> None:
    evidence = _bundle(walk_id, started_at, point)

    sheet = build_cellophane(evidence)

    assert sheet.walk_id == walk_id
    assert sheet.paint_fp == CANONICAL_PAINT_SPEC.fingerprint
    assert sheet.occupancy.keys() == sheet.peak.keys()
    assert math.fsum(sheet.occupancy.values()) == pytest.approx(
        math.fsum(segment.dt for segment in evidence.segments),
        rel=1e-12,
        abs=1e-9,
    )


@pytest.mark.parametrize("radius_u", [4.0, 8.0, 15.0])
def test_mass_is_not_duplicated_by_grid_resolution(radius_u, walk_id, started_at, point) -> None:
    evidence = _bundle(walk_id, started_at, point)
    spec = paint_spec(radius_u, NARROW_STEP, 1.5)

    sheet = paint_sheet(walk_id, started_at, evidence.segments, spec)

    assert math.fsum(sheet.occupancy.values()) == pytest.approx(60.0)


def test_occupancy_and_peak_keep_different_meanings(walk_id, started_at, point) -> None:
    evidence = _bundle(walk_id, started_at, point)
    sheet = build_cellophane(evidence)

    assert any(
        amount > peak for cell, amount in sheet.occupancy.items() if (peak := sheet.peak[cell])
    )
    assert all(0 < peak <= 1 for peak in sheet.peak.values())


def test_brush_reach_is_real_metres_not_mercator_units() -> None:
    radius_u = 8.0
    flat_20m = BrushProfile("flat 20m", (20.0,), (1.0,))
    stamped = brush_stamp(LAT, LNG, radius_u, flat_20m)
    farthest = max(_ground_m((LAT, LNG), hex_center_latlng(*cell, radius_u)) for cell, _ in stamped)

    assert 20.0 - cell_size_m(radius_u, LAT) <= farthest <= 20.0
    assert farthest > 17.0


def test_explicit_chain_break_is_not_painted_as_a_route(walk_id, started_at, point) -> None:
    evidence = _bundle(walk_id, started_at, point, chain_break=True)
    sheet = build_cellophane(evidence)
    midpoint = point(65, 150.0, seq=999)

    assert len({segment.chain_index for segment in evidence.segments}) == 2
    assert hex_cell(midpoint.lat, midpoint.lng, sheet.radius_u) not in sheet.occupancy


def test_same_evidence_produces_the_same_sheet(walk_id, started_at, point) -> None:
    evidence = _bundle(walk_id, started_at, point)
    assert build_cellophane(evidence) == build_cellophane(evidence)


def test_paint_fingerprint_tracks_resolved_conditions() -> None:
    integer_spelling = paint_spec(8, NARROW_STEP, 2)
    float_spelling = paint_spec(8.0, NARROW_STEP, 2.0)
    changed_step = paint_spec(8.0, NARROW_STEP, 2.5)

    assert integer_spelling == float_spelling
    assert integer_spelling.fingerprint == float_spelling.fingerprint
    assert changed_step.fingerprint != integer_spelling.fingerprint


def test_cellophane_rejects_a_forged_calculation_identity() -> None:
    with pytest.raises(ValueError, match="paint_fp"):
        Cellophane(
            walk_id=uuid.uuid4(),
            at=datetime(2026, 9, 1, tzinfo=UTC),
            radius_u=CANONICAL_RADIUS_U,
            profile=NARROW_STEP.name,
            occupancy={(0, 0): 1.0},
            peak={(0, 0): 1.0},
            paint_version=2,
            grid_version=GRID_VERSION,
            profile_fp=NARROW_STEP.fingerprint,
            sample_step_m=CANONICAL_SAMPLE_STEP_M,
            paint_fp="forged",
        )
