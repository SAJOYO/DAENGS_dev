from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from daengs_journey.core.clock import now_utc
from daengs_journey.journey.contract import Companion
from daengs_journey.journey.engine import snapshot
from daengs_journey.journey.models import Transport
from daengs_journey.journey.planning import resolve_journey
from daengs_journey.journey.state import EditableState, JourneyPrefs
from daengs_journey.providers.base import LatLng

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
    dog_id: str | None = Field(None, max_length=128)
    state: EditableState | None = None
    prefs: JourneyPrefs | None = None
    measured: bool = True
    with_polyline: bool = True
    arrive_note: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def supported_contract(self) -> "JourneyIn":
        if self.state is not None and self.prefs is not None:
            raise ValueError("send state or legacy prefs, not both")
        if self.dog_id is not None and self.dog_id.strip():
            raise ValueError("dog_id requires a profile source and is not supported")
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
    if body.state is not None:
        state = body.state.model_copy(deep=True)
        state.lat, state.lng = body.origin
    else:
        state = EditableState(
            lat=body.origin[0],
            lng=body.origin[1],
            journey=body.prefs or JourneyPrefs(),
        )
    plan = resolve_journey(
        state,
        now=now,
        companion=body.companion,
        measured=body.measured,
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
