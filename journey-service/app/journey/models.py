from datetime import datetime

from pydantic import BaseModel, Field

from app.journey.contract import Companion
from app.providers.base import Mode, RouteStatus


class SpotOut(BaseModel):
    kind: str
    lat: float
    lng: float
    offset_m: int
    text: str
    landmark: str = ""
    road: str = ""
    big_road: bool = False
    length_m: int = 0
    note: str | None = None
    warn: bool = False


class Handoff(BaseModel):
    naver: str
    kakao: str
    tmap: str


class RoadMix(BaseModel):
    big_road_m: int = 0
    total_m: int = 0
    big_ratio: float = 0.0
    big_crossings: int = 0


class Leg(BaseModel):
    status: RouteStatus = "estimate"
    status_reason: str | None = None
    min: int | None = None
    provider_min: int | None = None
    m: int | None = None
    source: str = "none"
    facilities: dict[str, int | float] | None = None
    road_mix: RoadMix | None = None
    taxi_fare: int | None = None
    fare: int | None = None
    advice: str | None = None
    why: list[str] = Field(default_factory=list)
    spots: list[SpotOut] = Field(default_factory=list)
    polyline: str | None = None
    polyline_points: int = 0
    handoff: Handoff | None = None


class Transport(BaseModel):
    as_of: datetime
    companion: Companion
    straight_m: int
    mode_priority: list[Mode] = Field(default_factory=list)
    walk: Leg | None = None
    car: Leg | None = None
    transit: Leg | None = None
