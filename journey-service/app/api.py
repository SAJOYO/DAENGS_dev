from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.clock import now_utc
from app.journey.contract import Companion, JourneyPlan
from app.journey.engine import snapshot
from app.journey.models import Transport
from app.providers.base import LatLng

router = APIRouter(prefix="/journey", tags=["journey"])
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
Origin = tuple[Latitude, Longitude]


class Dest(BaseModel):
    """DAENGS_APP sends canonical Place coordinates, never a service-local DB id."""

    model_config = ConfigDict(extra="forbid")

    lat: Latitude
    lng: Longitude
    name: str = Field("", max_length=200)


class JourneyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Origin
    dests: list[Dest] = Field(min_length=1, max_length=10)
    companion: Companion = "dog"
    # Kept for APP wire compatibility. This service deliberately has no profile source, so both an
    # absent id and an id use DAENGS_geo's profile-free path.
    dog_id: str | None = Field(None, max_length=128)
    measured: bool = True
    with_polyline: bool = True
    arrive_note: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def normalize_dog_id(self) -> "JourneyIn":
        if self.dog_id is not None and not self.dog_id.strip():
            self.dog_id = None
        return self


class JourneyItem(BaseModel):
    id: int | None = None
    name: str
    lat: float
    lng: float
    transport: Transport


class JourneyOut(BaseModel):
    companion: Companion
    items: list[JourneyItem]


@router.post("", response_model=JourneyOut)
async def journey(body: JourneyIn) -> JourneyOut:
    """Run the exact profile-free route used by the current APP request."""

    now = now_utc()
    plan = JourneyPlan(
        origin_lat=body.origin[0],
        origin_lng=body.origin[1],
        resolved_at=now,
        companion=body.companion,
        measured=body.measured,
        # DAENGS_geo resolve_request with no dog/owner profile makes these three available.
        mode_priority=("walk", "car", "transit"),
    )
    items = []
    for destination in body.dests:
        transport = await snapshot(
            plan,
            LatLng(destination.lat, destination.lng),
            dest_name=destination.name,
            with_polyline=body.with_polyline,
            arrive_note=body.arrive_note,
        )
        items.append(
            JourneyItem(
                name=destination.name,
                lat=destination.lat,
                lng=destination.lng,
                transport=transport,
            )
        )
    return JourneyOut(companion=body.companion, items=items)
