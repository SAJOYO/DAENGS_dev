import math

from app.providers.base import LatLng, Mode, RouteResult

DETOUR = {"walk": 1.3, "car": 1.4, "transit": 1.5}
SPEED_MPS = {"walk": 1.0, "car": 5.5, "transit": 4.0}


def haversine_m(a: LatLng, b: LatLng) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = math.radians(b.lat - a.lat), math.radians(b.lng - a.lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


class FakeProvider:
    """The deterministic estimate provider from DAENGS_geo c5f0d5f."""

    name = "fake"
    route_modes = frozenset({"walk", "car", "transit"})

    async def route(self, mode: Mode, origin: LatLng, dest: LatLng) -> RouteResult | None:
        straight = haversine_m(origin, dest)
        distance = straight * DETOUR[mode]
        duration = distance / SPEED_MPS[mode]
        taxi = fare = None
        if mode == "car":
            taxi = 4800 + max(0, int((distance - 1600) / 131)) * 100
        elif mode == "transit":
            fare = 1500
        return RouteResult(
            mode=mode,
            distance_m=int(distance),
            duration_s=int(duration),
            source="estimate",
            polyline=(origin, dest),
            facilities=None,
            taxi_fare=taxi,
            fare=fare,
        )
