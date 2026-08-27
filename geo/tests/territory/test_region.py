"""면 판정 기하 고정. 스파이크 측정치가 여기 정확성에 통째로 걸려 있다.

`scripts/spikes/territory_paint/region_fidelity.py` 가 "셀 근사가 얼마나 틀리나"를 재는데, 참값을 내는 것이
`region_encounters` 다. 이게 틀리면 오차표 전체가 틀린 값을 기준으로 잰 것이 된다.
그래서 손으로 답을 아는 배치 몇 개를 박아 둔다.
"""

import math
from datetime import UTC, datetime, timedelta

from app.features.territory.region import (
    Region,
    cell_visits,
    dwell_by_region,
    region_dwell_from_cells,
    region_encounters,
)
from app.features.walk.facts import compute_facts
from app.features.walk.models import WalkFix
from app.geo.cells import cell_id, hex_cell

EARTH_R = 6_371_000.0
LAT0, LNG0 = 37.4979, 127.0276
START = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def latlng(x: float, y: float) -> tuple[float, float]:
    return (
        LAT0 + math.degrees(y / EARTH_R),
        LNG0 + math.degrees(x / (EARTH_R * math.cos(math.radians(LAT0)))),
    )


def square(side: float, region_id: str = "park") -> Region:
    h = side / 2
    return Region(
        id=region_id, version=1,
        ring=tuple(latlng(x, y) for x, y in ((-h, -h), (h, -h), (h, h), (-h, h))),
    )


def segments_through(points: list[tuple[float, float]], step_s: float = 1.0):
    """경유점(미터) → 1m/s 로 걸은 segments. 거리(m)와 시간(s)이 1:1 이라 답이 손으로 나온다."""
    fixes = []
    t = 0.0
    for index in range(len(points) - 1):
        (x1, y1), (x2, y2) = points[index], points[index + 1]
        leg = math.hypot(x2 - x1, y2 - y1)
        count = max(1, round(leg / step_s))
        for k in range(count):
            frac = k / count
            lat, lng = latlng(x1 + (x2 - x1) * frac, y1 + (y2 - y1) * frac)
            fixes.append(WalkFix(client_seq=len(fixes), chain_index=0,
                                 at=START + timedelta(seconds=t),
                                 lat=lat, lng=lng, accuracy_m=3.0, is_mock=False))
            t += step_s
    lat, lng = latlng(*points[-1])
    fixes.append(WalkFix(client_seq=len(fixes), chain_index=0,
                         at=START + timedelta(seconds=t),
                         lat=lat, lng=lng, accuracy_m=3.0, is_mock=False))
    ended = START + timedelta(seconds=t + 1)
    return compute_facts("t", "dog", START, ended, fixes).segments


def test_straight_crossing_dwell_equals_chord_length():
    """1m/s 로 100m 면을 정중앙 관통하면 체류는 100초다. 경계 통과는 선분 위에서 보간한다."""
    segments = segments_through([(-120.0, 0.0), (120.0, 0.0)])
    total = dwell_by_region(region_encounters(segments, [square(100.0)]))["park"]
    # 정확히 100.0 이다. fix 간격(1m)보다 정밀한 것은 경계 통과 시각을 선분 위에서
    # 보간하기 때문 — 점만 세면 99 나 101 이 나온다.
    assert total == 100.0


def test_never_entering_yields_no_encounter():
    segments = segments_through([(-200.0, 300.0), (200.0, 300.0)])
    assert region_encounters(segments, [square(100.0)]) == []


def test_round_trip_makes_two_occurrences_not_one():
    """들어갔다 나갔다 다시 들어가면 행이 둘이다 — #59 가 v1 에서 못 했던 구분."""
    segments = segments_through([
        (-120.0, 0.0), (120.0, 0.0),      # 1회 통과
        (120.0, 200.0), (-120.0, 200.0),  # 바깥으로 크게 돌아
        (-120.0, 0.0), (120.0, 0.0),      # 2회 통과
    ])
    found = region_encounters(segments, [square(100.0)])
    assert [e.occurrence_index for e in found] == [0, 1]
    assert all(e.entry_observed and e.exit_observed for e in found)


def test_entry_not_observed_when_walk_starts_inside():
    """면 안에서 시작하면 진입을 못 봤다고 말한다. 모름을 관측으로 위조하지 않는다."""
    segments = segments_through([(0.0, 0.0), (200.0, 0.0)])
    found = region_encounters(segments, [square(100.0)])
    assert len(found) == 1
    assert found[0].entry_observed is False
    assert found[0].exit_observed is True


def test_region_version_travels_with_the_answer():
    """같은 모양이라도 버전이 다르면 답도 그 버전을 달고 나온다."""
    segments = segments_through([(-120.0, 0.0), (120.0, 0.0)])
    v2 = Region(id="park", version=2, ring=square(100.0).ring)
    assert region_encounters(segments, [v2])[0].region_version == 2


def test_cell_id_carries_radius():
    """반지름이 다르면 다른 격자다. id 가 그걸 말해야 섞이지 않는다."""
    cell = hex_cell(LAT0, LNG0, 28.0)
    assert cell_id(cell, 28.0).startswith("hex:28:")
    assert cell_id(cell, 28.0) != cell_id(hex_cell(LAT0, LNG0, 115.0), 115.0)


def test_cell_approximation_tracks_exact_when_cells_are_small():
    """셀이 면보다 충분히 작으면(비 ≥ 5) 근사가 정밀값을 따라간다.

    2026-08-26 측정의 손익분기를 테스트로 박는다 — 이 관계가 깨지면 셀 층의 근거가 깨진다.
    """
    region = square(400.0)
    segments = segments_through([(-260.0, -100.0), (260.0, -100.0)])
    exact = dwell_by_region(region_encounters(segments, [region]))["park"]
    approx = region_dwell_from_cells(cell_visits(segments, 28.0), region, 28.0)
    assert abs(approx - exact) / exact < 0.05


def test_cell_approximation_collapses_when_cells_are_large():
    """반대 방향도 고정한다 — 큰 셀이 조용히 그럴듯한 답을 내지 않는다는 것이 근거다."""
    region = square(100.0)
    segments = segments_through([(-120.0, 0.0), (120.0, 0.0)])
    exact = dwell_by_region(region_encounters(segments, [region]))["park"]
    approx = region_dwell_from_cells(cell_visits(segments, 250.0), region, 250.0)
    assert abs(approx - exact) / exact > 0.3
