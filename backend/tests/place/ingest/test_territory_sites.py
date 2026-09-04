"""140u 점령지 선별의 결정론과 식별자 계약."""

from datetime import date

import pytest

from daengs_place.ingest.territory_sites import replace, select, validate_site_count
from daengs_place.territory.grid import TERRITORY_SITE_RADIUS_U, hex_cell, site_id


def _lamp(lat: float, lng: float, kind: str = "한전주") -> dict:
    return {"lat": lat, "lng": lng, "kind": kind, "instt": None, "as_of": None}


def test_selection_keeps_one_site_per_cell_and_uses_active_generation() -> None:
    lamps = [_lamp(37.5 + index * 0.00001, 127.0) for index in range(8)]
    picked = select(lamps)

    assert len({row["site_id"] for row in picked}) == len(picked)
    assert all(row["site_id"].startswith("territory-site:hex-v1:140:") for row in picked)


def test_kepco_pole_wins_inside_the_same_cell() -> None:
    lat, lng = 37.5, 127.0
    picked = select([_lamp(lat, lng, "전용주"), _lamp(lat, lng, "한전주")])

    target_id = site_id(hex_cell(lat, lng), TERRITORY_SITE_RADIUS_U)
    assert [row["kind"] for row in picked if row["site_id"] == target_id] == ["한전주"]


def test_selection_is_independent_of_input_order() -> None:
    lamps = [_lamp(37.5 + index * 0.0004, 127.0 + index * 0.0003) for index in range(30)]

    first = {(row["site_id"], row["lat"], row["lng"]) for row in select(lamps)}
    second = {(row["site_id"], row["lat"], row["lng"]) for row in select(reversed(lamps))}

    assert first == second


def test_exact_coordinate_tie_uses_metadata_instead_of_input_order() -> None:
    older = {
        **_lamp(37.5, 127.0),
        "instt": "가 기관",
        "as_of": date(2024, 1, 1),
    }
    newer = {
        **_lamp(37.5, 127.0),
        "instt": "나 기관",
        "as_of": date(2025, 1, 1),
    }

    assert select([older, newer]) == select([newer, older])
    assert select([older, newer])[0]["as_of"] == date(2025, 1, 1)


async def test_empty_snapshot_cannot_delete_the_current_gameboard() -> None:
    with pytest.raises(ValueError, match="빈 점령지 스냅샷"):
        await replace([])


def test_fixed_snapshot_requires_the_exact_selected_site_count() -> None:
    validate_site_count(362_309, expected=362_309)

    with pytest.raises(ValueError, match="예상 건수 362,309개와 다릅니다"):
        validate_site_count(362_308, expected=362_309)


def test_snapshot_below_the_production_floor_is_rejected() -> None:
    with pytest.raises(ValueError, match="교체 하한 300,000개보다 작습니다"):
        validate_site_count(299_999)
