"""Facility discovery HTTP contract. No imports from the separate Place process."""

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

PlaceKind = Literal[
    "hospital",
    "pharmacy",
    "pet_shop",
    "shopping",
    "grooming",
    "boarding",
    "travel",
    "leisure",
    "museum",
    "gallery",
    "arts_center",
    "culture",
    "cafe",
    "restaurant",
    "pension",
    "hotel",
    "stay",
    "etc",
]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Spatial(InputModel):
    lat: float = Field(ge=32, le=40)
    lng: float = Field(ge=123, le=133)
    radius_m: int = Field(ge=100, le=20000)


class DogSnapshot(InputModel):
    ref: str = Field(min_length=1, max_length=100)
    revision: str | None = Field(None, max_length=100)
    dog_size: Literal["small", "medium", "large"] | None = None
    dog_weight_kg: float | None = Field(None, gt=0, le=200)
    dog_age_years: float | None = Field(None, ge=0, le=40)


class Preferences(InputModel):
    parking: bool = False


class FacilityDiscoveryRequest(InputModel):
    client_request_id: UUID
    query: str = Field(min_length=1, max_length=1000)
    spatial: Spatial
    kinds: list[PlaceKind] = Field(default_factory=list, max_length=6)
    preferences: Preferences = Field(default_factory=Preferences)
    dogs: list[DogSnapshot] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        if not self.query.strip():
            raise ValueError("query must not be blank")
        if len(set(self.kinds)) != len(self.kinds):
            raise ValueError("kinds must be unique")
        if len({dog.ref for dog in self.dogs}) != len(self.dogs):
            raise ValueError("dog refs must be unique")
        return self


class Applied(InputModel):
    kinds: list[PlaceKind]
    parking: bool


class Lens(InputModel):
    id: str
    label: str
    note: str
    applied: Applied
    # Place owns and validates the canonical public PlaceSearchResponse and presentation.
    # Backend preserves these payloads, without importing the Place runtime or reinterpreting facts.
    search: dict[str, Any]
    presentations: list[dict[str, Any]]


class Option(InputModel):
    id: str
    label: str
    availability: str
    note: str


class Signal(InputModel):
    id: str
    label: str
    state: str
    required: bool
    note: str
    options: list[Option]
    selected_option_id: str | None = None


class ConfirmAction(InputModel):
    type: Literal["confirm"]
    lens_id: str = Field(min_length=1, max_length=160)


class RefineAction(InputModel):
    type: Literal["refine"]
    signal_id: str = Field(min_length=1, max_length=160)
    option_id: str = Field(min_length=1, max_length=120)


class FacilityActionRequest(InputModel):
    client_request_id: UUID
    search_id: UUID
    expected_revision: int = Field(ge=1)
    action: ConfirmAction | RefineAction = Field(discriminator="type")


class Notice(InputModel):
    code: str
    message: str


class FacilityDiscoveryResponse(InputModel):
    contract_version: Literal["facility-discovery-v1"]
    search_id: UUID
    request: FacilityDiscoveryRequest
    outcome: Literal["results", "empty", "needs_clarification", "unsupported"]
    lenses: list[Lens] = Field(max_length=3)
    signals: list[Signal] = Field(max_length=20)
    notices: list[Notice]
    confirmed_lens_id: str | None = None
    revision: int = Field(default=1, ge=1)
    expires_at: datetime | None = None
    action_request: FacilityActionRequest | None = None


class FacilityInternalResponse(InputModel):
    result: FacilityDiscoveryResponse
    # Opaque Place-owned data, stored and returned only over internal HTTP.
    continuation: dict[str, Any]
