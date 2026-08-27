"""네이버 클라우드 Maps — Static Map + Geocoding/Reverse Geocoding.

Static Map: 마커 최대 20개, w/h 1~1024. 헤더 인증이라 URL만 넘기면 클라이언트가 못 받는다
→ static_map_url은 우리 서버의 프록시 경로를 돌려주고, 실제 호출은 서버가 한다 (키 노출 방지).
"""

from urllib.parse import urlencode

import httpx

from app.providers.base import LatLng, Mode, RouteResult, StaticMapSpec

# Application Services > Maps 게이트웨이. 구 "AI·NAVER API > 지도"(naveropenapi.apigw.ntruss.com)는
# 2025-03 신규 차단됐고, 현행 키로 부르면 401 "A subscription to the API is required" 가 온다.
# 실측 2026-08-20: 같은 키·같은 헤더·같은 경로에서 호스트만 바꾸면 200 (docs/research/…-pricing.md).
APIGW = "https://maps.apigw.ntruss.com"


class NaverProvider:
    name = "naver"
    route_modes: frozenset = frozenset()       # 경로 미구현 — 정적지도·지오코딩만

    def __init__(self, key_id: str, key: str, client: httpx.AsyncClient | None = None):
        self._headers = {
            "x-ncp-apigw-api-key-id": key_id,
            "x-ncp-apigw-api-key": key,
        }
        self._client = client or httpx.AsyncClient(timeout=5.0)

    # --- static map -----------------------------------------------------
    def upstream_static_url(self, spec: StaticMapSpec) -> str:
        """서버가 직접 호출할 네이버 원본 URL (헤더 필요)."""
        markers = "|".join(
            f"type:{'t' if m.highlight else 'd'}|size:mid|pos:{m.pos.lng} {m.pos.lat}"
            + (f"|label:{m.label}" if m.label else "")
            for m in spec.markers[:20]
        )
        q = {
            "w": spec.width,
            "h": spec.height,
            "center": f"{spec.center.lng},{spec.center.lat}",
            "level": spec.zoom,
        }
        if markers:
            q["markers"] = markers
        return f"{APIGW}/map-static/v2/raster?{urlencode(q, safe='|:, ')}"

    def static_map_url(self, spec: StaticMapSpec) -> str | None:
        # 클라이언트에 주는 건 우리 프록시 경로. 파라미터는 spec을 그대로 직렬화.
        q = {
            "lat": spec.center.lat,
            "lng": spec.center.lng,
            "zoom": spec.zoom,
            "w": spec.width,
            "h": spec.height,
            "m": ",".join(f"{m.pos.lat}:{m.pos.lng}:{m.label}:{int(m.highlight)}" for m in spec.markers),
        }
        return f"/map/static?{urlencode(q)}"

    async def fetch_static_png(self, spec: StaticMapSpec) -> bytes:
        r = await self._client.get(self.upstream_static_url(spec), headers=self._headers)
        r.raise_for_status()
        return r.content

    # --- geocoding ------------------------------------------------------
    async def geocode(self, address: str) -> LatLng | None:
        r = await self._client.get(
            f"{APIGW}/map-geocode/v2/geocode",
            params={"query": address, "count": 1},
            headers=self._headers,
        )
        r.raise_for_status()
        addrs = r.json().get("addresses") or []
        if not addrs:
            return None
        return LatLng(lat=float(addrs[0]["y"]), lng=float(addrs[0]["x"]))

    async def reverse_geocode(self, pos: LatLng) -> str | None:
        r = await self._client.get(
            f"{APIGW}/map-reversegeocode/v2/gc",
            params={
                "coords": f"{pos.lng},{pos.lat}",
                "orders": "roadaddr,addr",
                "output": "json",
            },
            headers=self._headers,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            return None
        region = results[0].get("region", {})
        parts = [region.get(f"area{i}", {}).get("name", "") for i in range(1, 5)]
        return " ".join(p for p in parts if p) or None

    async def route(self, mode: Mode, origin: LatLng, dest: LatLng) -> RouteResult | None:
        # TODO: 자동차 = 카카오모빌리티 Directions / 네이버 Directions 5. 키 발급 후.
        return None
