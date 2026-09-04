import httpx
import pytest

from daengs_journey.providers.base import LatLng
from daengs_journey.providers.tmap import TmapProvider
from daengs_journey.providers.tmap_parse import parse_tmap


def tmap_response() -> dict:
    return {
        "features": [
            {
                "geometry": {"type": "Point", "coordinates": [127.0, 37.5]},
                "properties": {
                    "totalDistance": 300,
                    "totalTime": 240,
                    "turnType": 200,
                },
            },
            {
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[127.0, 37.5], [127.001, 37.501]],
                },
                "properties": {"distance": 200, "name": "테헤란로"},
            },
            {
                "geometry": {"type": "Point", "coordinates": [127.001, 37.501]},
                "properties": {"turnType": 211, "intersectionName": "사거리"},
            },
            {
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[127.001, 37.501], [127.002, 37.502]],
                },
                "properties": {"distance": 100, "name": "강남대로"},
            },
        ]
    }


def test_tmap_parser_keeps_measured_route_facts() -> None:
    route = parse_tmap(tmap_response())

    assert route.source == "tmap"
    assert route.distance_m == 300
    assert route.duration_s == 240
    assert route.facilities is not None
    assert route.facilities.crosswalk == 1
    assert route.facilities.big_crossings == 1
    assert route.spots[0].kind == "crosswalk"


@pytest.mark.asyncio
async def test_tmap_request_keeps_original_coordinate_order_and_recommended_route() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        assert body["startX"] == 127.0
        assert body["startY"] == 37.5
        assert body["endX"] == 127.1
        assert body["endY"] == 37.6
        assert body["searchOption"] == 0
        assert request.headers["appKey"] == "secret"
        return httpx.Response(200, json=tmap_response())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TmapProvider("secret", client)

    route = await provider.route("walk", LatLng(37.5, 127.0), LatLng(37.6, 127.1))

    assert route is not None
    assert route.source == "tmap"
    await client.aclose()
