"""면 방문률 계약. `app/features/territory/layers.region_visit_rate`.

화면이 쓸 문장은 이것이다.

    최근 30일 산책의 42% · 저녁 산책에선 71%

이 문장이 뜻하는 것은 **산책을 센 비율**이다. 그런데 이미 있던 `mass_in` 은 물감 질량의
배분을 준다 — 처음에 그걸 쓰려다 잡혔다. 두 값이 얼마나 다른지를 이 파일이 고정한다.

## 왜 이 구별이 화면에서 위험한가

틀린 숫자에 **맞는 문장**이 붙기 때문이다. `mass_in` 이 0.9 를 주면 "산책의 90%가 방문"
이라고 쓰게 되는데, 실제로는 한 번 가서 오래 머문 것일 수 있다. 사람이 화면을 보고
검산할 방법이 없다.
"""

import math
from datetime import UTC, datetime, timedelta

from app.features.territory.layers import (
    Aggregation,
    LayerSpec,
    Projection,
    Selector,
    mass_in,
    rate_field,
    region_visit_rate,
    render,
)
from app.features.territory.paint import NARROW_STEP, paint_sheet
from app.features.territory.region import Region
from app.features.walk.facts import compute_facts
from app.features.walk.models import WalkFix

EARTH_R = 6_371_000.0
RADIUS_U = 8.0
LAT, LNG = 37.4979, 127.0276
BRUSH, BRUSH_FP = NARROW_STEP.name, NARROW_STEP.fingerprint

# 면은 원점에서 **동쪽 200m 부터** 시작한다. 그래서 서쪽으로만 걸은 산책은 안 닿는다.
PARK_WEST_M = 200.0
PARK_EAST_M = 3_600.0


def _east(x_m: float) -> float:
    return LNG + math.degrees(x_m / (EARTH_R * math.cos(math.radians(LAT))))


def _north(y_m: float) -> float:
    return LAT + math.degrees(y_m / EARTH_R)


PARK = Region(
    id="yangjaecheon",
    version=1,
    ring=(
        (_north(-120.0), _east(PARK_WEST_M)),
        (_north(-120.0), _east(PARK_EAST_M)),
        (_north(120.0), _east(PARK_EAST_M)),
        (_north(120.0), _east(PARK_WEST_M)),
    ),
)


def _walk(walk_id: str, at: datetime, start_m: float, end_m: float, *,
          offset_m: float = 0.0):
    """`start_m` 에서 `end_m` 까지 동서로 걷는 산책 하나. 1m/s, 1Hz."""
    lat = _north(offset_m)
    step = 1.0 if end_m >= start_m else -1.0
    xs = [start_m + step * i for i in range(int(abs(end_m - start_m)) + 1)]
    fixes = [
        WalkFix(client_seq=i, chain_index=0, at=at + timedelta(seconds=i),
                lat=lat, lng=_east(x), accuracy_m=3.0, is_mock=False)
        for i, x in enumerate(xs)
    ]
    segs = compute_facts("w", "d", fixes[0].at, fixes[-1].at + timedelta(seconds=1),
                         fixes).segments
    return paint_sheet(walk_id, at, segs, RADIUS_U, NARROW_STEP)


def _spec(min_peak: float = 0.0, **tags) -> LayerSpec:
    return LayerSpec(
        selector=Selector.of(**tags),
        aggregation=Aggregation(metric="walks", min_peak=min_peak),
        projection=Projection(radius_u=RADIUS_U, brush=BRUSH, profile_fp=BRUSH_FP),
    )


JULY = datetime(2026, 7, 1, 9, tzinfo=UTC)


def _three_walks():
    """리뷰가 준 반례 그대로.

        A 산책: 공원 안을 3km      → 방문
        B 산책: 공원 안을 100m     → 방문
        C 산책: 공원 밖에서만      → 미방문

    방문률은 2/3 이어야 한다. 물감 질량으로 세면 A 가 B 를 30 배로 눌러 전혀 다른 답이 된다.
    """
    return [
        _walk("A", JULY, PARK_WEST_M + 100, PARK_WEST_M + 3_100),
        _walk("B", JULY + timedelta(days=1), PARK_WEST_M + 100, PARK_WEST_M + 200),
        _walk("C", JULY + timedelta(days=2), -50.0, -900.0),
    ]


def test_visit_rate_counts_walks_not_paint():
    """**이 파일의 핵심.** 3km 산책과 100m 산책이 똑같이 1 회다."""
    rate = region_visit_rate(_three_walks(), _spec(), PARK)
    assert (rate.visited, rate.selected) == (2, 3)
    assert rate.rate == 2 / 3


def _mass_and_rate(sheets):
    spec = _spec()
    layer = render(sheets, spec)
    park_cells = {cell for cell in layer.canvas if _in_park(cell)}
    return mass_in(rate_field(layer), park_cells), region_visit_rate(sheets, spec, PARK).rate


def test_mass_in_cannot_stand_in_for_visit_rate():
    """**왜 `mass_in` 을 못 쓰는지.** 방문률이 똑같은 두 자료가 질량에서는 갈린다.

    구현이 아니라 **구별이 실재함**을 보이는 테스트다. 두 자료 다 "셋 중 둘이 공원에 갔다"
    인데, 공원 안에서 걸은 거리만 다르다.

        긴 산책   A 3km + B 100m + C 밖   →  방문 2/3
        짧은 산책 A 100m + B 100m + C 밖  →  방문 2/3

    방문률은 둘 다 0.667 이어야 하고, 질량은 달라야 한다. 질량이 방문률의 대용이 될 수
    있었다면 두 값이 같이 움직였을 것이다.
    """
    long_visit = _three_walks()
    short_visit = [
        _walk("A", JULY, PARK_WEST_M + 100, PARK_WEST_M + 200),
        _walk("B", JULY + timedelta(days=1), PARK_WEST_M + 100, PARK_WEST_M + 200),
        _walk("C", JULY + timedelta(days=2), -50.0, -900.0),
    ]

    long_mass, long_rate = _mass_and_rate(long_visit)
    short_mass, short_rate = _mass_and_rate(short_visit)

    assert long_rate == short_rate == 2 / 3, "방문률은 두 자료에서 같아야 한다"
    assert long_mass - short_mass > 0.3, (
        f"질량이 안 갈리면 이 구별을 테스트할 수 없다: {long_mass=} {short_mass=}")
    # 그리고 어느 쪽도 방문률과 안 맞는다 — 우연히 비슷한 값이 나오는 것도 아니다
    assert abs(long_mass - long_rate) > 0.1
    assert abs(short_mass - short_rate) > 0.1


def _in_park(cell) -> bool:
    from app.features.territory.region import _point_in_ring, _projector
    from app.geo.cells import hex_center_latlng
    project = _projector(PARK.ring[0][0], PARK.ring[0][1])
    ring = [project(lat, lng) for lat, lng in PARK.ring]
    return _point_in_ring(*project(*hex_center_latlng(*cell, RADIUS_U)), ring)


def test_the_denominator_is_the_selected_walks_not_all_walks():
    """조건을 걸면 분모도 같이 줄어든다 — 비율을 견줄 수 있는 유일한 방법이다."""
    sheets = _three_walks()
    everything = region_visit_rate(sheets, _spec(), PARK)
    assert (everything.selected, everything.total) == (3, 3)

    # 7월 1일 하루만 — A 만 남는다
    one_day = LayerSpec(
        selector=Selector.of(since=JULY.date(), until=JULY.date()),
        aggregation=Aggregation(metric="walks"),
        projection=Projection(radius_u=RADIUS_U, brush=BRUSH, profile_fp=BRUSH_FP),
    )
    narrowed = region_visit_rate(sheets, one_day, PARK)
    assert (narrowed.visited, narrowed.selected, narrowed.total) == (1, 1, 3)
    assert narrowed.rate == 1.0


def test_the_numerator_and_denominator_survive_in_the_result():
    """비율만 들고 다니면 안 된다 — 5/7 과 17/24 는 화면에서 같은 문장이 된다."""
    rate = region_visit_rate(_three_walks(), _spec(), PARK)
    assert rate.visited == 2 and rate.selected == 3
    assert rate.region_id == "yangjaecheon" and rate.region_version == 1


def _grazer():
    """면 **남쪽 경계 8m 바깥**을 500m 나란히 지나간 산책.

    발은 한 번도 면 안에 없다(y = -128, 면은 -120 까지). 그런데 붓 도달이 20m 라 물감은
    면 안 12m 까지 들어간다.

    긴 변을 따라 나란히 두는 이유가 있다. 처음엔 서쪽 **끝**으로 다가가게 짰는데 통과했다 —
    붓이 199.3m 까지 갔는데 면이 200m 부터라, 그 5m 안에 **칸 중심이 하나도 없었다.** 격자
    양자화가 우연히 가려 준 것이지 안 닿은 게 아니다. 긴 변을 따라가면 x 범위 전체에 칸이
    깔려서 그 우연이 안 생긴다.
    """
    return _walk("graze", JULY, PARK_WEST_M + 300, PARK_WEST_M + 800, offset_m=-128.0)


def test_a_walk_that_only_grazes_the_edge_is_a_visit_at_min_peak_zero():
    """붓이 번져 닿기만 해도 문턱 0 에서는 방문이다. **그 사실을 숨기지 않는다.**"""
    touched = region_visit_rate([_grazer()], _spec(min_peak=0.0), PARK)
    assert touched.visited == 1, "붓이 면에 닿았는데 방문으로 안 셌다"


def test_raising_min_peak_drops_the_grazing_walk():
    """문턱을 올리면 **심 안까지 들어온 산책만** 남는다.

    이 손잡이가 있을 수 있는 것은 결정 #69 가 `peak` 을 버리지 않은 덕이다 —
    `occupancy` 만 남겼으면 "닿았다" 와 "들어왔다" 를 나중에 가를 수 없다.

    문턱 값을 여기서 정하지 않는다. 등급은 territory-paint §C 의 열린 결정이고, 이 테스트는
    **손잡이가 실제로 듣는지**만 고정한다.
    """
    grazer = _grazer()
    walker = _walk("inside", JULY, PARK_WEST_M + 100, PARK_WEST_M + 400)

    strict = _spec(min_peak=0.9)
    assert region_visit_rate([grazer], strict, PARK).visited == 0
    assert region_visit_rate([walker], strict, PARK).visited == 1

    both = region_visit_rate([grazer, walker], strict, PARK)
    assert (both.visited, both.selected) == (1, 2)


def test_min_peak_defaults_to_the_aggregation_the_layer_uses():
    """기본 문턱은 spec 을 따라간다 — 겹치기와 다른 눈으로 세면 지도와 숫자가 어긋난다."""
    sheets = _three_walks()
    assert region_visit_rate(sheets, _spec(min_peak=0.4), PARK).min_peak == 0.4
    assert region_visit_rate(sheets, _spec(min_peak=0.4), PARK, min_peak=0.0).min_peak == 0.0


def test_no_selected_walks_has_no_rate_instead_of_claiming_zero_percent():
    """0/0 은 미방문 0/10 과 다르므로 화면에 0%라고 전달하지 않는다."""
    empty = LayerSpec(
        selector=Selector.of(since=datetime(2030, 1, 1, tzinfo=UTC).date()),
        aggregation=Aggregation(metric="walks"),
        projection=Projection(radius_u=RADIUS_U, brush=BRUSH, profile_fp=BRUSH_FP),
    )
    rate = region_visit_rate(_three_walks(), empty, PARK)
    assert (rate.visited, rate.selected, rate.total) == (0, 0, 3)
    assert rate.rate is None
