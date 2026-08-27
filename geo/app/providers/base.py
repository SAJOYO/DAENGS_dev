"""지도 제공사 어댑터 — docs/explorations/map-provider/, transport-snapshot.md

백엔드가 제공사를 만지는 곳: 정적 지도 URL · 지오코딩 · 경로. 그래서 4메서드.
타일·렌더링·턴바이턴 안내는 클라이언트/제공사 앱 몫이므로 여기 없다.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol

Mode = Literal["walk", "car", "transit"]

# 이 leg 의 숫자가 어디서 왔나. **source(누가) 와 다른 축이다 — status 는 얼마나 믿을 수 있나.**
#   measured     제공사가 실제로 준 값
#   estimate     직선거리 × 우회계수 ÷ 속도. 호출자가 요청했거나(목록 미리보기) 실측이 실패해 강등됐거나
#   unavailable  이 수단을 안 쓰기로 설정했다. **숫자를 만들지 않는다** (min·m 이 null)
RouteStatus = Literal["measured", "estimate", "unavailable"]


@dataclass(frozen=True)
class LatLng:
    lat: float
    lng: float


@dataclass(frozen=True)
class MapMarker:
    pos: LatLng
    label: str = ""
    highlight: bool = False


@dataclass(frozen=True)
class StaticMapSpec:
    center: LatLng
    zoom: int = 16
    width: int = 600
    height: int = 300
    markers: tuple[MapMarker, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Facilities:
    """도보 경로 위 시설 집계.

    실측(2026-08-19, 강남역→논현): 횡단보도는 포인트 turnType 211~217, 지하보도/육교는 LineString facilityType
    14/18/12 '구간'으로 온다 (연속 구간 = 통로 하나로 합쳐 센다). 계단/경사로/엘리베이터는 포인트 127/129/128/218.
    """

    crosswalk: int = 0
    stairs: int = 0
    underpass: int = 0        # 통로 수 (연속 구간 병합, 출발 통로 제외)
    underpass_m: int = 0      # 지하 구간 총 길이 (출발 통로 제외)
    origin_passage_m: int = 0 # 출발 직후 짧은 지하 통로(역 출구 등). 장애물로 안 센다
    overpass: int = 0
    elevator: int = 0
    slope: int = 0
    # 길의 성격 — 큰길(대로/로) 구간 비율. 소음·차·밝기의 대리 지표. 계단보다 매번 있는 진짜 축
    big_road_m: int = 0
    total_m: int = 0
    big_road_ratio: float = 0.0
    big_crossings: int = 0


@dataclass(frozen=True)
class Spot:
    """반려견 관심 지점 — 출발 전 한 장에 찍히는 것. 네비 스텝이 아니다.

    kind: crosswalk | stairs | underpass | overpass | elevator | slope | origin_passage | arrive
    """

    kind: str
    at: LatLng
    offset_m: int                 # 출발로부터 경로상 거리
    text: str                     # "버거킹 차병원사거리점 앞 횡단보도 (논현로)"
    landmark: str = ""            # nearPoiName / intersectionName
    road: str = ""                # 관련 도로명
    big_road: bool = False        # 대로급 횡단
    length_m: int = 0             # 지하 통로 등 구간 길이


@dataclass(frozen=True)
class RouteResult:
    mode: Mode
    distance_m: int
    duration_s: int
    source: str                              # "estimate" | "tmap" | "naver" | ...
    polyline: tuple[LatLng, ...] = ()
    facilities: Facilities | None = None      # walk만
    taxi_fare: int | None = None              # car만
    fare: int | None = None                   # transit만
    spots: tuple[Spot, ...] = ()              # walk만. 순서 있음


class MapProvider(Protocol):
    name: str
    # **실제로 구현한** 경로 모드. 설정만 받고 None 을 돌려주면 사용자에겐 조용한 추정으로 보인다
    # (kakao 자동차가 그랬다) — 그래서 능력을 선언하게 하고 시작 시 설정과 대조한다.
    route_modes: frozenset[Mode]

    def static_map_url(self, spec: StaticMapSpec) -> str | None: ...
    async def geocode(self, address: str) -> LatLng | None: ...
    async def reverse_geocode(self, pos: LatLng) -> str | None: ...
    async def route(self, mode: Mode, origin: LatLng, dest: LatLng) -> RouteResult | None: ...


class NullProvider:
    """제공사 미설정. 지도·경로 없이도 검색은 동작해야 한다."""

    name = "none"
    route_modes: frozenset[Mode] = frozenset()

    def static_map_url(self, spec: StaticMapSpec) -> str | None:
        return None

    async def geocode(self, address: str) -> LatLng | None:
        return None

    async def reverse_geocode(self, pos: LatLng) -> str | None:
        return None

    async def route(self, mode: Mode, origin: LatLng, dest: LatLng) -> RouteResult | None:
        return None
