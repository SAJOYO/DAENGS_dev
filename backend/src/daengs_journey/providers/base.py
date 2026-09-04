from dataclasses import dataclass
from typing import Literal, Protocol

Mode = Literal["walk", "car", "transit"]
RouteStatus = Literal["measured", "estimate", "unavailable"]


@dataclass(frozen=True)
class LatLng:
    lat: float
    lng: float


@dataclass(frozen=True)
class Facilities:
    crosswalk: int = 0
    stairs: int = 0
    underpass: int = 0
    underpass_m: int = 0
    origin_passage_m: int = 0
    overpass: int = 0
    elevator: int = 0
    slope: int = 0
    big_road_m: int = 0
    total_m: int = 0
    big_road_ratio: float = 0.0
    big_crossings: int = 0


@dataclass(frozen=True)
class Spot:
    kind: str
    at: LatLng
    offset_m: int
    text: str
    landmark: str = ""
    road: str = ""
    big_road: bool = False
    length_m: int = 0


@dataclass(frozen=True)
class RouteResult:
    mode: Mode
    distance_m: int
    duration_s: int
    source: str
    polyline: tuple[LatLng, ...] = ()
    facilities: Facilities | None = None
    taxi_fare: int | None = None
    fare: int | None = None
    spots: tuple[Spot, ...] = ()


class RouteProvider(Protocol):
    name: str
    route_modes: frozenset[Mode]

    async def route(self, mode: Mode, origin: LatLng, dest: LatLng) -> RouteResult | None: ...


class NullProvider:
    name = "none"
    route_modes: frozenset[Mode] = frozenset()

    async def route(self, mode: Mode, origin: LatLng, dest: LatLng) -> RouteResult | None:
        return None
