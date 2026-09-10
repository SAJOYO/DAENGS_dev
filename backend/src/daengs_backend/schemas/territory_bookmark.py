"""Private saved-site contracts. No dog, season or notification subscription required."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from daengs_backend.schemas.territory_claim import SiteId
from daengs_backend.schemas.territory_owned import OwnedTerritoryLocation


class TerritoryBookmarkItem(BaseModel):
    site_id: SiteId
    created_at: datetime
    location: OwnedTerritoryLocation | None
    location_status: Literal["AVAILABLE", "NOT_FOUND", "UNAVAILABLE"]


class TerritoryBookmarkList(BaseModel):
    total_count: int = Field(ge=0)
    limit: int = Field(ge=1)
    items: list[TerritoryBookmarkItem]


class TerritoryBookmarkState(BaseModel):
    site_id: SiteId
    is_bookmarked: bool
    created_at: datetime | None
    total_count: int = Field(ge=0)
    limit: int = Field(ge=1)
