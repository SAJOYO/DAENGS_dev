"""결정론 가짜 제공사. 키 없이 끝까지 돌리기 위한 것.

경로 = 직선 × 우회계수 ÷ 속도. **시설(횡단보도·계단·지하보도)은 만들지 않는다.**

거리·시간·요금은 모델이다 — "약 12분"은 정직하게 라벨을 붙이면 쓸모가 있다. 시설은 모델이
아니라 자리채우기였다 ("400m마다 횡단보도 1개"). 그게 `walk_advice` 로 흘러 들어가서,
TMAP 이 죽은 날 노령·관절견의 경로에 **실측된 적 없는 "계단 1회 — 노령" 경고**가 붙었다.
결정 #21 이 "폴백은 시간·거리만, 틀린 시설정보는 없는 것보다 나쁨" 이라 못박은 그 지점이다.

도보 옵션이라는 개념 자체는 결정 #66 으로 없앴다. 여기 있던 "옵션이 거리를 안 바꾼다"는
설명도 그때 같이 사라졌다 — 바꾸지 않을 옵션이 없다.
"""

import math

from app.providers.base import LatLng, Mode, RouteResult, StaticMapSpec

DETOUR = {"walk": 1.3, "car": 1.4, "transit": 1.5}
SPEED_MPS = {"walk": 1.0, "car": 5.5, "transit": 4.0}   # 도보 3.6km/h(횡단 대기 포함), 차 도심 20km/h


def haversine_m(a: LatLng, b: LatLng) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = math.radians(b.lat - a.lat), math.radians(b.lng - a.lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class FakeProvider:
    name = "fake"
    route_modes = frozenset({"walk", "car", "transit"})

    def static_map_url(self, spec: StaticMapSpec) -> str | None:
        return None

    async def geocode(self, address: str) -> LatLng | None:
        return None

    async def reverse_geocode(self, pos: LatLng) -> str | None:
        return None

    async def route(self, mode: Mode, origin: LatLng, dest: LatLng) -> RouteResult | None:
        straight = haversine_m(origin, dest)
        dist = straight * DETOUR[mode]
        dur = dist / SPEED_MPS[mode]
        taxi = fare = None
        if mode == "car":
            taxi = 4800 + max(0, int((dist - 1600) / 131)) * 100   # 서울 기본요금 근사
        elif mode == "transit":
            fare = 1500
        return RouteResult(
            mode=mode, distance_m=int(dist), duration_s=int(dur), source="estimate",
            polyline=(origin, dest), facilities=None, taxi_fare=taxi, fare=fare,
        )
