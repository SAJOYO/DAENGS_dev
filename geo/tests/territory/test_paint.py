"""붓과 셀로판 계약. `app/features/territory/paint.py`.

이 파일이 지키는 것 둘이 리뷰에서 나온 실제 버그다.

1. **거리는 실제 지상 미터다.** 격자는 Web Mercator 라 위도 37.5° 에서 1 단위가 0.79m 다.
   되돌리지 않으면 `3·8·20` 밴드가 실제 2.4·6.3·15.9m 로 동작한다.
2. **셀로판을 미리 접으면 안 된다.** 칸마다 peak 최대값 하나로 접는 순간 "한 번 밟고
   49 번 옆으로 지나감" 이 "50 번 다 밟음" 과 구별되지 않는다.
"""

import math
from datetime import UTC, datetime, timedelta

from app.features.territory.paint import (
    NARROW_SMOOTH,
    NARROW_STEP,
    brush_stamp,
    canvas_stats,
    flat,
    paint_sheet,
    stack,
)
from app.features.walk.facts import compute_facts
from app.features.walk.models import WalkFix
from app.geo.cells import cell_area_m2, cell_size_m, hex_cell, hex_center_latlng

EARTH_R = 6_371_000.0
START = datetime(2026, 7, 1, tzinfo=UTC)
RADIUS_U = 8.0
LAT, LNG = 37.4979, 127.0276


def _straight_walk(day: int, offset_m: float, lat0: float, lng0: float, length_m: int = 60):
    """`offset_m` 만큼 남/북으로 떨어진 동서 직선을 1m/s 로 걷는다."""
    fixes = []
    for i in range(length_m + 1):
        lat = lat0 + math.degrees(offset_m / EARTH_R)
        lng = lng0 + math.degrees((i - length_m / 2) / (EARTH_R * math.cos(math.radians(lat0))))
        fixes.append(WalkFix(client_seq=i, chain_index=0,
                             at=START + timedelta(days=day, seconds=i),
                             lat=lat, lng=lng, accuracy_m=3.0, is_mock=False))
    ended = fixes[-1].at + timedelta(seconds=1)
    return compute_facts("w", "d", fixes[0].at, ended, fixes).segments


# ---- 붓 단면 -------------------------------------------------------------------------


def test_weight_falls_off_and_stops_at_reach():
    """중심에서 멀어질수록 물감이 준다. 도달 밖은 0 — '조금 묻음' 이 아니라 없음이다."""
    previous = NARROW_SMOOTH.weight_at(0.0)
    for distance in range(1, 21):
        current = NARROW_SMOOTH.weight_at(float(distance))
        assert current <= previous
        previous = current
    assert NARROW_SMOOTH.weight_at(20.0) > 0
    assert NARROW_SMOOTH.weight_at(20.001) == 0.0
    assert NARROW_SMOOTH.weight_at(100.0) == 0.0


def test_step_profile_holds_value_inside_each_band():
    """계단은 밴드 안에서 평평하다 — 등고선이 보이는 이유이자 연속과 갈리는 지점."""
    assert NARROW_STEP.weight_at(0.5) == NARROW_STEP.weight_at(3.0) == 1.0
    assert NARROW_STEP.weight_at(3.5) == NARROW_STEP.weight_at(8.0) == 0.45
    assert NARROW_SMOOTH.weight_at(5.0) != NARROW_SMOOTH.weight_at(8.0)


# ---- 단위: 실제 미터인가 (회귀) -------------------------------------------------------


def test_brush_reach_is_real_metres_not_grid_units():
    """붓 도달 반경이 **실제 지상 미터**여야 한다.

    격자 좌표를 그대로 거리로 쓰면 위도 37.5° 에서 20m 밴드가 실제 15.9m 로 줄어든다.
    가장 먼 칠해진 칸의 중심까지 실제 거리로 확인한다 — 셀 반지름만큼의 여유는 준다.
    """
    stamped = brush_stamp(LAT, LNG, RADIUS_U, flat(20.0))
    cell_m = cell_size_m(RADIUS_U, LAT)
    far = max(
        _ground_m((LAT, LNG), hex_center_latlng(*cell, RADIUS_U)) for cell, _ in stamped
    )
    assert 20.0 - cell_m <= far <= 20.0, f"가장 먼 칸 {far:.1f}m — 20m 밴드가 아니다"
    # 격자 단위를 그대로 썼다면 15.9m 근처에서 끝났을 것이다
    assert far > 17.0


def test_cell_area_is_ground_area():
    """넓이는 지상 면적이다. 격자 단위² 를 그대로 쓰면 위도 37.5° 에서 1.6배로 부푼다."""
    naive = 1.5 * math.sqrt(3) * RADIUS_U**2
    real = cell_area_m2(RADIUS_U, LAT)
    assert real < naive
    assert math.isclose(real / naive, math.cos(math.radians(LAT)) ** 2, rel_tol=1e-9)


def _ground_m(a, b) -> float:
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlat, dlng = lat2 - lat1, math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(h))


# ---- 셀로판을 접으면 안 된다 (회귀) ---------------------------------------------------


def test_one_direct_pass_is_not_hidden_by_many_near_passes():
    """**이 파일의 핵심.** 한 번 밟고 49 번 옆으로 지나간 칸.

    장을 유지하면 문턱으로 "밟은 건 1 회" 를 답할 수 있다. 칸마다 최대값 하나로 접으면
    `walks=50 · peak=1.0` 이 되어 "50 번 다 밟음" 과 구별되지 않는다.
    """
    target = hex_cell(LAT, LNG, RADIUS_U)
    clat, clng = hex_center_latlng(*target, RADIUS_U)

    sheets = [paint_sheet("w0", START, _straight_walk(0, 0.0, clat, clng),
                          RADIUS_U, NARROW_STEP)]
    sheets += [
        paint_sheet(f"w{d}", START + timedelta(days=d),
                    _straight_walk(d, 12.0, clat, clng), RADIUS_U, NARROW_STEP)
        for d in range(1, 50)
    ]

    loose = stack(sheets)[target]
    assert loose.walks == 50                       # 문턱 없이 세면 전부 세진다

    strict = stack(sheets, min_peak=0.9)[target]
    assert strict.walks == 1, "심 밴드에 든 산책은 한 번뿐이어야 한다"
    assert strict.peak == 1.0
    # 그리고 그 한 번의 물감만 남는다 — 49 번의 옅은 물감이 섞여 들어오지 않는다
    assert strict.occupancy < loose.occupancy


def test_threshold_is_a_query_not_a_stored_value():
    """같은 장 묶음에서 문턱만 바꾸면 다른 답이 나온다 — 다시 칠하지 않고."""
    target = hex_cell(LAT, LNG, RADIUS_U)
    clat, clng = hex_center_latlng(*target, RADIUS_U)
    sheets = [
        paint_sheet(f"w{d}", START + timedelta(days=d),
                    _straight_walk(d, offset, clat, clng), RADIUS_U, NARROW_STEP)
        for d, offset in enumerate([0.0, 0.0, 5.0, 12.0, 12.0])
    ]
    counts = [stack(sheets, min_peak=t).get(target) for t in (0.0, 0.4, 0.9)]
    walks = [c.walks if c else 0 for c in counts]
    assert walks == sorted(walks, reverse=True), f"문턱이 오르면 빈도는 준다: {walks}"
    assert walks[0] > walks[-1]


def _dwell_walk(offset_m: float, lat0: float, lng0: float, seconds: int):
    """`offset_m` 만큼 떨어진 한 점에 `seconds` 동안 서 있는다."""
    lat = lat0 + math.degrees(offset_m / EARTH_R)
    fixes = [
        WalkFix(client_seq=i, chain_index=0, at=START + timedelta(seconds=i),
                lat=lat, lng=lng0, accuracy_m=3.0, is_mock=False)
        for i in range(seconds + 1)
    ]
    ended = fixes[-1].at + timedelta(seconds=1)
    return compute_facts("w", "d", fixes[0].at, ended, fixes).segments


def test_occupancy_and_peak_answer_different_questions():
    """옆에서 **오래 머물면** 물감은 커도 세기는 낮게 남는다.

    지나가는 거리로는 안 된다 — 한 칸 근처에 있는 시간은 붓 반경과 속도가 정하지 산책
    길이가 정하지 않는다. 물감을 키우는 것은 거리가 아니라 체류다.
    """
    target = hex_cell(LAT, LNG, RADIUS_U)
    clat, clng = hex_center_latlng(*target, RADIUS_U)
    near = paint_sheet("near", START, _straight_walk(0, 0.0, clat, clng, 20),
                       RADIUS_U, NARROW_STEP)
    far = paint_sheet("far", START, _dwell_walk(12.0, clat, clng, 600),
                      RADIUS_U, NARROW_STEP)
    assert far.occupancy[target] > near.occupancy[target]      # 오래 머물러 물감은 더 많다
    assert far.peak[target] < near.peak[target]                # 그래도 가까이 온 적은 없다


# ---- 겹친 결과의 성질 ----------------------------------------------------------------


def test_canvas_stats_reports_ground_area_and_honest_core_count():
    target = hex_cell(LAT, LNG, RADIUS_U)
    clat, clng = hex_center_latlng(*target, RADIUS_U)
    sheets = [paint_sheet(f"w{d}", START + timedelta(days=d),
                          _straight_walk(d, 0.0, clat, clng), RADIUS_U, NARROW_STEP)
              for d in range(4)]
    canvas = stack(sheets)
    stats = canvas_stats(canvas, RADIUS_U, len(sheets))
    assert stats.cells == len(canvas)
    assert math.isclose(stats.area_m2, len(canvas) * cell_area_m2(RADIUS_U, LAT), rel_tol=1e-3)
    assert 0 < stats.core_hit_cells <= stats.cells


def test_flat_profile_makes_every_cell_look_like_a_core_hit():
    """이진 도장이 왜 기각됐는지를 테스트로 남긴다 — 구별할 값이 생기지 않는다."""
    clat, clng = hex_center_latlng(*hex_cell(LAT, LNG, RADIUS_U), RADIUS_U)
    sheets = [paint_sheet("w0", START, _straight_walk(0, 0.0, clat, clng),
                          RADIUS_U, flat(25.0))]
    canvas = stack(sheets)
    stats = canvas_stats(canvas, RADIUS_U, 1)
    assert stats.core_hit_cells == stats.cells
    assert stats.fringe_cells == 0
