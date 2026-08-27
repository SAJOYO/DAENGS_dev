"""카카오 — 로컬 API(지오코딩) + 정적 지도(2026-07-21 신규 REST).

지오코딩 응답의 x=경도, y=위도. 순서 주의.
정적 지도 REST는 신규라 파라미터 형식이 바뀔 수 있다 — 키 발급 후 문서 재확인.
"""

import httpx

from app.providers.base import LatLng, Mode, RouteResult, StaticMapSpec

LOCAL_BASE = "https://dapi.kakao.com/v2/local"


class KakaoProvider:
    name = "kakao"
    route_modes: frozenset = frozenset()       # 경로 미구현 — 로컬(지오코딩)만

    def __init__(self, rest_key: str, client: httpx.AsyncClient | None = None):
        self._headers = {"Authorization": f"KakaoAK {rest_key}"}
        self._client = client or httpx.AsyncClient(timeout=5.0)

    def static_map_url(self, spec: StaticMapSpec) -> str | None:
        # TODO(kakao static map): 신규 REST 엔드포인트/파라미터 확정 후 구현.
        # 그전까지는 None → 클라이언트가 자체 렌더 또는 카드 이미지 생략.
        return None

    async def geocode(self, address: str) -> LatLng | None:
        r = await self._client.get(
            f"{LOCAL_BASE}/search/address.json",
            params={"query": address, "size": 1},
            headers=self._headers,
        )
        r.raise_for_status()
        docs = r.json().get("documents") or []
        if not docs:
            return None
        return LatLng(lat=float(docs[0]["y"]), lng=float(docs[0]["x"]))

    async def reverse_geocode(self, pos: LatLng) -> str | None:
        r = await self._client.get(
            f"{LOCAL_BASE}/geo/coord2address.json",
            params={"x": pos.lng, "y": pos.lat},
            headers=self._headers,
        )
        r.raise_for_status()
        docs = r.json().get("documents") or []
        if not docs:
            return None
        road = docs[0].get("road_address") or {}
        jibun = docs[0].get("address") or {}
        return road.get("address_name") or jibun.get("address_name")

    async def route(self, mode: Mode, origin: LatLng, dest: LatLng) -> RouteResult | None:
        # TODO: 자동차 = 카카오모빌리티 Directions / 네이버 Directions 5. 키 발급 후.
        return None
