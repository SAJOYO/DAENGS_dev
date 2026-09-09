"""Public card fields only; never expose activity source/session references."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class OwnerSeasonRecord(BaseModel):
    points: str = Field(pattern=r"^\d+(\.\d)?$")
    owned_site_count: int = Field(ge=0)
    score_as_of_ms: int = Field(ge=0)


class PublicTerritoryOwner(BaseModel):
    pet_id: uuid.UUID
    name: str
    is_mine: bool
    certification: Literal["UNVERIFIED", "VERIFIED"]
    occupied_at: datetime
    season_record: OwnerSeasonRecord | None


class TerritoryOwnerSummary(BaseModel):
    site_id: str
    version: int = Field(ge=0)
    server_now_ms: int = Field(ge=0)
    season_id: str | None
    status: Literal["READY", "PENDING", "UNOCCUPIED", "NO_ACTIVE_SEASON"]
    owner: PublicTerritoryOwner | None
