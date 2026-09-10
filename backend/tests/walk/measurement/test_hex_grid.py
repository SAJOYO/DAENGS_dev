import json
import math

import pytest

from daengs_walk.hex_grid import (
    GRID_VERSION,
    cell_area_m2,
    hex_boundary_latlng,
    hex_cell,
    hex_center_latlng,
)
from tests.walk.support.paths import WALK_FIXTURES

GOLDEN = WALK_FIXTURES / "hex-grid-golden.json"


def test_hex_v1_matches_the_geo_golden_vector() -> None:
    contract = json.loads(GOLDEN.read_text(encoding="utf-8"))

    assert contract["grid_version"] == GRID_VERSION
    assert len(contract["cases"]) == 28
    for case in contract["cases"]:
        actual = hex_cell(case["lat"], case["lng"], case["radius_u"])
        assert actual == (case["q"], case["r"]), case


def test_golden_vector_covers_latitudes_and_resolutions() -> None:
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    assert max(case["lat"] for case in cases) - min(case["lat"] for case in cases) > 30
    assert len({case["radius_u"] for case in cases}) == 4


def test_cell_boundary_and_area_are_ground_geometry() -> None:
    lat, lng, radius_u = 37.4979, 127.0276, 8.0
    cell = hex_cell(lat, lng, radius_u)
    centre = hex_center_latlng(*cell, radius_u)
    boundary = hex_boundary_latlng(*cell, radius_u)

    assert len(boundary) == 6
    assert centre not in boundary
    projected_area = 1.5 * math.sqrt(3) * radius_u**2
    assert cell_area_m2(radius_u, lat) == pytest.approx(
        projected_area * math.cos(math.radians(lat)) ** 2
    )


@pytest.mark.parametrize("radius_u", [0.0, -1.0, math.inf, math.nan])
def test_invalid_radius_is_rejected(radius_u: float) -> None:
    with pytest.raises(ValueError):
        hex_cell(37.5, 127.0, radius_u)
