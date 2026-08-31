import httpx

from daengs_journey.providers.base import LatLng, Mode, RouteResult
from daengs_journey.providers.tmap_parse import parse_tmap

URL = "https://apis.openapi.sk.com/tmap/routes/pedestrian"


class TmapProvider:
    """TMAP pedestrian route adapter from DAENGS_geo c5f0d5f."""

    name = "tmap"
    route_modes = frozenset({"walk"})

    def __init__(self, app_key: str, client: httpx.AsyncClient | None = None):
        self._headers = {"appKey": app_key, "Content-Type": "application/json"}
        self._client = client or httpx.AsyncClient(timeout=8.0)

    async def route(self, mode: Mode, origin: LatLng, dest: LatLng) -> RouteResult | None:
        if mode != "walk":
            return None
        body = {
            "startX": origin.lng,
            "startY": origin.lat,
            "endX": dest.lng,
            "endY": dest.lat,
            "reqCoordType": "WGS84GEO",
            "resCoordType": "WGS84GEO",
            "startName": "출발",
            "endName": "도착",
            "searchOption": 0,
        }
        response = await self._client.post(
            URL, json=body, headers=self._headers, params={"version": 1}
        )
        response.raise_for_status()
        return parse_tmap(response.json())
