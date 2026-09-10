"""Public game views for authenticated members; no private pet or walk fields."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from daengs_backend.schemas.territory_claim import SiteId


class GamePet(BaseModel):
    pet_id: uuid.UUID
    name: str
    breed: str | None
    is_mine: bool
    has_photo: bool
    photo_updated_at: datetime | None


class GameSeason(BaseModel):
    id: str
    starts_ms: int
    ends_ms: int
    policy_version: str
    revision: int
    score_as_of_ms: int


class GameRecord(BaseModel):
    rank: int = Field(ge=1)
    points: str
    base_points: int | None
    takeover_points: int | None
    holding_points: str
    owned_site_count: int = Field(ge=0)
    verified_site_count: int = Field(ge=0)
    score_as_of_ms: int


class GameStanding(BaseModel):
    pet: GamePet
    season_record: GameRecord


class GameLeaderboard(BaseModel):
    status: Literal["READY", "NO_ACTIVE_SEASON"]
    server_now_ms: int
    season: GameSeason | None
    total_count: int = Field(ge=0)
    items: list[GameStanding]
    next_cursor: str | None


class GameProfile(BaseModel):
    status: Literal["READY", "NOT_PARTICIPATING", "NO_ACTIVE_SEASON"]
    server_now_ms: int
    season: GameSeason | None
    pet: GamePet
    season_record: GameRecord | None


class LeaderboardCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    kind: Literal["game_leaderboard"] = "game_leaderboard"
    season: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    units: str = Field(pattern=r"^\d{1,100}$")
    pet: uuid.UUID


class PublicSitesCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    kind: Literal["game_pet_sites"] = "game_pet_sites"
    season: str = Field(min_length=1, max_length=128)
    pet: uuid.UUID
    after: SiteId
