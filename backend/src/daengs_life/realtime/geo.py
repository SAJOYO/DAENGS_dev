"""좌표 변환·거리 — 어느 API 의 것도 아닌 계산 (RT-001 ①).

`providers/` 밖에 있는 이유는 **자체 계산이 기본 경로이기 때문**이다. 카카오 `transcoord` 로도
되고 에어코리아 `getNearbyMsrstnList` 로도 되지만, 그러면 `KAKAO_REST_KEY` 없이는 관통이 안 되고
호출 예산(개발계정 일 1,000회, RT-001 ④-e)을 좌표 변환에 쓰게 된다. 여기서 계산하면 카카오는
"○○동" 표기라는 UX 역할만 남는다 — 실측으로 강화된 결론이다 (`docs/realtime-apis.md` §6.5).

이 모듈은 **설정도 네트워크도 안 본다.** 순수 함수뿐이라 키 없이 테스트된다.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from typing import NamedTuple, TypeVar


class LatLon(NamedTuple):
    """위경도. **튜플이 아니라 이름을 쓰는 이유가 실측에 있다.**

    에어코리아 `getMsrstnList` 는 측정소 좌표를 `dmX`·`dmY` 로 주는데 **`dmX` 가 위도다**
    (§6.5). 이름과 내용이 어긋나는 값이 실제로 들어오므로, 경계에서 한 번 이름을 붙여 두면
    그 뒤로는 순서를 헷갈릴 자리가 없다. `(lon, lat)` 로 뒤집힌 실수는 조용히 틀린 격자를
    내놓고 아무 예외도 안 난다.
    """

    lat: float
    lon: float


class Grid(NamedTuple):
    """기상청 단기예보 격자. 5km 간격이고 **정의상 내 위치를 포함한다** (RT-001 ⑤-d)."""

    nx: int
    ny: int


# --- 기상청 단기예보 활용가이드의 LCC(Lambert Conformal Conic) 파라미터 ---
# 값은 전부 가이드가 고정한 것이다. 하나라도 우리가 고른 숫자가 아니다 (③-d 의 비인용 금지와 같은 기준).
_RE_KM = 6371.00877     # 이 투영이 쓰는 지구 반경
_GRID_KM = 5.0          # 격자 간격
_SLAT1, _SLAT2 = 30.0, 60.0     # 표준위도 1·2
_OLON, _OLAT = 126.0, 38.0      # 기준점 경위도
_XO, _YO = 43, 136              # 기준점의 격자 좌표

_DEG = math.pi / 180
_re = _RE_KM / _GRID_KM
_slat1, _slat2 = _SLAT1 * _DEG, _SLAT2 * _DEG
_olon, _olat = _OLON * _DEG, _OLAT * _DEG

_sn = math.log(math.cos(_slat1) / math.cos(_slat2)) / math.log(
    math.tan(math.pi * 0.25 + _slat2 * 0.5) / math.tan(math.pi * 0.25 + _slat1 * 0.5))
_sf = (math.tan(math.pi * 0.25 + _slat1 * 0.5) ** _sn) * math.cos(_slat1) / _sn
_ro = _re * _sf / (math.tan(math.pi * 0.25 + _olat * 0.5) ** _sn)


def to_grid(p: LatLon) -> Grid:
    """위경도 → 격자. 검산은 `tests/test_geo.py` 의 4지점 (검문소 A).

    한반도 밖은 정의하지 않는다 — 범위 검사를 넣지 않은 것은 의도다. "어디까지가 한반도인가"의
    경계 상자는 우리가 만드는 숫자이고, 그런 값은 근거 없이 두지 않는다 (③-d).
    """
    ra = _re * _sf / (math.tan(math.pi * 0.25 + p.lat * _DEG * 0.5) ** _sn)
    theta = p.lon * _DEG - _olon
    # 날짜변경선을 건너도 각이 한 바퀴 어긋나지 않게 (-pi, pi] 로 접는다
    theta = (theta + math.pi) % (2 * math.pi) - math.pi
    theta *= _sn
    # 가이드는 `(int)(x + 0.5)` — 반올림이다. 한반도에서는 값이 양수라 floor 와 같고,
    # floor 는 음수에서 int() 처럼 0 쪽으로 튀지 않아 경계 동작이 한 가지로 남는다.
    return Grid(math.floor(ra * math.sin(theta) + _XO + 0.5),
                math.floor(_ro - ra * math.cos(theta) + _YO + 0.5))


def to_latlon(g: Grid) -> LatLon:
    """격자 → 그 격자의 대표점(중심). `to_grid` 의 역이고 왕복이 안정적이다.

    쓰는 곳은 두 군데다 — 격자를 사람에게 보여줄 때, 그리고 `to_grid` 가 정말 가역인지
    테스트가 확인할 때. 예보 조회 자체는 `(nx, ny)` 를 그대로 넘기므로 이 함수를 안 탄다.
    """
    xn, yn = g.nx - _XO, _ro - (g.ny - _YO)
    ra = math.hypot(xn, yn)
    if _sn < 0:
        ra = -ra
    alat = 2 * math.atan((_re * _sf / ra) ** (1 / _sn)) - math.pi * 0.5
    if abs(xn) <= 0:
        theta = 0.0
    elif abs(yn) <= 0:
        theta = math.copysign(math.pi * 0.5, xn)
    else:
        theta = math.atan2(xn, yn)
    return LatLon(alat / _DEG, (theta / _sn + _olon) / _DEG)


def same_grid(a: LatLon, b: LatLon) -> bool:
    """두 지점이 같은 예보 격자에 있는가 — **출처 우선순위의 판정식** (RT-001 ⑤-d).

    ⑤-d 가 `"가까우면 AWS"` 가 아니라 `"같은 격자면 AWS"` 를 고른 이유가 여기 있다.
    "5km 안" 같은 임계를 두면 근거 없는 숫자가 하나 더 생기지만, 격자 일치는 **이미 정의된
    단위**라 새 숫자를 만들지 않는다. 지점이 내 격자 안이면 대표성 문제가 사라지고
    신선도 이득만 남으므로 그때는 분 단위 AWS 관측이 명백히 낫다.
    """
    return to_grid(a) == to_grid(b)


def haversine_km(a: LatLon, b: LatLon) -> float:
    """두 지점의 대권 거리(km).

    지구 반경은 위 LCC 파라미터의 것을 그대로 쓴다. 물리 상수를 따로 두면 "지구 반경은 얼마인가"의
    답이 이 파일 안에서 둘이 되는데, 쓰임이 **최근접 측정소 고르기**라 6371.0088 과 6371.00877 의
    차이는 순위를 절대 못 바꾼다. 답이 하나인 쪽이 낫다.
    """
    p1, p2 = a.lat * _DEG, b.lat * _DEG
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin((b.lon - a.lon) * _DEG / 2) ** 2)
    return 2 * _RE_KM * math.asin(math.sqrt(h))


T = TypeVar("T")


def nearest(origin: LatLon, candidates: Iterable[T],
            key: Callable[[T], LatLon]) -> tuple[T, float] | None:
    """가장 가까운 후보와 그 거리(km). 후보가 없으면 `None`.

    거리를 같이 돌려주는 이유 — 부르는 쪽이 그 값을 다시 필요로 한다 (⑥ 응답의 `sources` 가
    "측정소 ○○ (3.2km)" 를 싣는다). 항목만 돌려주면 호출자가 `haversine_km` 을 한 번 더 부르고,
    그 순간 "얼마나 먼가"의 답이 두 군데서 계산된다.

    측정소 목록은 `getMsrstnList` 를 `data/reference/` 에 캐시해 둔 것을 쓴다 — 그래서 이
    함수 하나로 `transcoord → getNearbyMsrstnList` 두 번의 호출이 사라진다 (§6.5).
    """
    best: tuple[T, float] | None = None
    for item in candidates:
        km = haversine_km(origin, key(item))
        if best is None or km < best[1]:
            best = (item, km)
    return best
