"""Current member-owned sites, independent of viewport and walking sessions."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from daengs_backend.schemas.territory_claim import SiteId


class OwnedTerritoryLocation(BaseModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)


class OwnedTerritory(BaseModel):
    site_id: SiteId
    version: int = Field(ge=0)
    pet_id: uuid.UUID
    pet_name: str
    pet_breed: str | None
    certification: Literal["UNVERIFIED", "VERIFIED"]
    occupied_at: datetime
    expires_at: datetime | None
    location: OwnedTerritoryLocation | None
    location_status: Literal["AVAILABLE", "NOT_FOUND"]


class OwnedTerritoryPage(BaseModel):
    status: Literal["READY", "NO_ACTIVE_SEASON"]
    season_id: str | None
    server_now_ms: int
    pet_id: uuid.UUID | None
    total_count: int = Field(ge=0)
    items: list[OwnedTerritory]
    next_cursor: str | None


class OwnedTerritoryCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    owner: uuid.UUID
    pet: uuid.UUID | None
    season: str = Field(min_length=1, max_length=128)
    after: SiteId
