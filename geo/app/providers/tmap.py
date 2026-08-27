"""TMAP 보행자 경로 (SK오픈API). research/2026-08-19-route-apis.md

searchOption: 0 추천 · 4 추천+대로우선 · 10 최단 · 30 최단거리+계단제외
facilityType(구간): 12 육교 · 14 지하보도 · 15 횡단보도 · 18 지하철지하보도 · (17: 문서 없음, 일반 인도로 취급)
turnType(포인트): 125 육교 · 126 지하보도 · 127 계단 · 128 경사로 · 129 계단+경사로 · 211~217 횡단보도 · 218 엘리베이터
totalDistance/totalTime은 첫 Feature properties에만.

실측(2026-08-19): 지하보도는 turnType 126이 아니라 LineString facilityType 14 구간으로 왔음. 도보 속도 ~4.4km/h(낙관적).
계단제외(30)는 거리·시간이 늘고 지하보도 구간이 많아진다 — 옵션 간 트레이드오프를 응답에 실을 것.
"""

import httpx

from app.providers.base import LatLng, Mode, RouteResult, StaticMapSpec
from app.providers.tmap_parse import parse_tmap

URL = "https://apis.openapi.sk.com/tmap/routes/pedestrian"
class TmapProvider:
    name = "tmap"
    route_modes = frozenset({"walk"})          # 보행자 경로만. 자동차·대중교통 없음

    def __init__(self, app_key: str, client: httpx.AsyncClient | None = None):
        self._headers = {"appKey": app_key, "Content-Type": "application/json"}
        self._client = client or httpx.AsyncClient(timeout=8.0)

    def static_map_url(self, spec: StaticMapSpec) -> str | None:
        return None

    async def geocode(self, address: str) -> LatLng | None:
        return None

    async def reverse_geocode(self, pos: LatLng) -> str | None:
        return None

    async def route(self, mode: Mode, origin: LatLng, dest: LatLng) -> RouteResult | None:
        if mode != "walk":
            return None
        body = {
            "startX": origin.lng, "startY": origin.lat,
            "endX": dest.lng, "endY": dest.lat,
            "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO",
            "startName": "출발", "endName": "도착",
            # 결정 #66 — **추천 경로 하나만 요청한다.** 생략해도 지금은 같은 결과지만,
            # 그건 TMAP 기본값이 0 이라는 외부 사실에 기대는 것이다. 우리 정책은 "옵션 없음"이
            # 아니라 "추천을 쓴다" 이므로 요청 본문이 그렇게 말하게 둔다.
            "searchOption": 0,
        }
        r = await self._client.post(URL, json=body, headers=self._headers, params={"version": 1})
        r.raise_for_status()
        return parse_tmap(r.json())
