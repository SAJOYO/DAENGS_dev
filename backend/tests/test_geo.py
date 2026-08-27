"""검문소 A — 격자 변환이 기상청 격자표와 맞는가 (RT-001 구현 계획 3).

4지점은 `docs/realtime-apis.md` §6.5 에서 실제로 대조한 것이다. 여기 박아 두는 이유는
**변환식은 조용히 틀리기 때문**이다 — 한 칸 어긋난 격자도 예외 없이 정상 응답을 받고,
날씨가 대충 비슷해서 눈으로는 안 잡힌다. 실제로 이 구현도 첫 시도에서 네 지점 모두
(+1, +1) 로 어긋났다 (`+1.5` 반올림은 `XO=42/YO=135` 와 짝이고, `XO=43/YO=136` 은 `+0.5` 다).
"""
from __future__ import annotations

import math

from daengs_life.realtime.geo import Grid, LatLon, haversine_km, nearest, same_grid, to_grid, to_latlon

# (이름, 위경도, 기상청 격자표의 값) — §6.5
POINTS = [
    ("서울 강남구", LatLon(37.4979, 127.0276), Grid(61, 125)),
    ("서울 종로구", LatLon(37.5729, 126.9794), Grid(60, 127)),
    ("부산 해운대", LatLon(35.1631, 129.1637), Grid(99, 75)),
    ("제주시", LatLon(33.4996, 126.5312), Grid(53, 38)),
]


def test_grid_matches_the_kma_table() -> None:
    wrong = [f"{name}: {to_grid(p)} != {expected}"
             for name, p, expected in POINTS if to_grid(p) != expected]
    assert not wrong, "격자 변환이 기상청 표와 어긋난다.\n  " + "\n  ".join(wrong)


def test_grid_roundtrip_is_stable() -> None:
    """격자 → 대표점 → 격자가 제자리로 온다.

    앞 테스트만으로는 4지점을 우연히 맞히는 상수 조합도 통과할 수 있다. 왕복은 변환이
    가역이라는 것 — 즉 대표점이 정말 그 격자 안이라는 것 — 을 4지점 밖에서도 요구한다.
    """
    for name, p, _ in POINTS:
        g = to_grid(p)
        assert to_grid(to_latlon(g)) == g, name


def test_representative_point_is_inside_its_own_cell() -> None:
    """대표점이 원래 지점에서 격자 한 칸(5km) 안쪽이다."""
    for name, p, _ in POINTS:
        assert haversine_km(p, to_latlon(to_grid(p))) < 5.0, name


def test_same_grid_is_not_the_same_as_nearby() -> None:
    """⑤-d 의 판정이 '가깝다'가 아니라 '같은 칸'이라는 것.

    실물로 확인된다 — 에어코리아 강남구 측정소(§6.5 응답의 dmX/dmY)는 강남 기준점에서
    2km 남짓인데 **격자는 다르다.** '5km 안이면 같은 곳' 같은 임계를 뒀다면 여기서
    갈렸을 자리다.
    """
    gangnam = LatLon(37.4979, 127.0276)
    station = LatLon(37.515336, 127.049357)          # dmX 가 위도다 (§6.5)
    assert haversine_km(gangnam, station) < 5.0
    assert to_grid(station) != to_grid(gangnam)
    assert not same_grid(gangnam, station)
    assert same_grid(gangnam, gangnam)


def test_haversine_against_an_exact_case() -> None:
    """적도 90° = 원둘레의 1/4. 손으로 만든 기대값 없이 검산할 수 있는 유일한 지점이다."""
    quarter = haversine_km(LatLon(0.0, 0.0), LatLon(0.0, 90.0))
    assert math.isclose(quarter, 6371.00877 * math.pi / 2, rel_tol=1e-12)
    assert haversine_km(LatLon(37.5, 127.0), LatLon(37.5, 127.0)) == 0.0


def test_haversine_agrees_with_a_flat_approximation_at_city_scale() -> None:
    """짧은 거리에서는 평면 근사와 붙어야 한다 — 식을 잘못 옮겼는지 잡는 독립 검사."""
    a, b = LatLon(37.4979, 127.0276), LatLon(37.5729, 126.9794)
    lat_mid = math.radians((a.lat + b.lat) / 2)
    flat = 6371.00877 * math.hypot(math.radians(b.lat - a.lat),
                                   math.radians(b.lon - a.lon) * math.cos(lat_mid))
    assert math.isclose(haversine_km(a, b), flat, rel_tol=1e-4)


def test_nearest_returns_the_item_and_its_distance() -> None:
    stations = [
        ("종로구", LatLon(37.5729, 126.9794)),
        ("강남구", LatLon(37.515336, 127.049357)),
        ("제주시", LatLon(33.4996, 126.5312)),
    ]
    hit = nearest(LatLon(37.4979, 127.0276), stations, key=lambda s: s[1])
    assert hit is not None
    station, km = hit
    assert station[0] == "강남구"
    # 거리를 같이 받으므로 호출자가 haversine 을 다시 부를 일이 없다
    assert math.isclose(km, haversine_km(LatLon(37.4979, 127.0276), station[1]))


def test_nearest_on_empty_is_none() -> None:
    """측정소 목록 캐시가 비어 있는 경우 — 예외가 아니라 None 이라야 ⑤ 저하가 UNKNOWN 을 만든다."""
    assert nearest(LatLon(37.5, 127.0), [], key=lambda s: s) is None
