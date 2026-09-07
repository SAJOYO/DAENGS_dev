"""Owner-scoped activity read contracts. No raw GPS, photo URLs or peer identities."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class WalkWindow(BaseModel):
    from_ms: int = Field(ge=0, le=253402300799000)
    to_ms: int = Field(ge=0, le=253402300799000)
    pet_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def window(self):
        if not 0 < self.to_ms - self.from_ms <= 366 * 86_400_000:
            raise ValueError("window must be positive and at most 366 days")
        return self


class SessionLinkResponse(BaseModel):
    client_session_id: uuid.UUID
    walk_id: uuid.UUID | None
    game_session_id: uuid.UUID | None
    status: Literal["WAITING_FOR_WALK", "WALK_ONLY", "LINKED", "CONFLICT"]
    conflicts: list[str]


class ProjectionIdentity(BaseModel):
    generation_id: str
    statistics_version: str


class AnalysisVersions(BaseModel):
    facts: int
    calculation: int
    receipt: int
    capsule: int


class WalkSourceRef(BaseModel):
    walk_id: uuid.UUID
    analysis_id: uuid.UUID
    revision: int


class WalkSummaryResponse(BaseModel):
    identity: ProjectionIdentity
    expected_versions: AnalysisVersions
    owner_id: uuid.UUID
    pet_id: uuid.UUID | None
    from_ms: int
    to_ms: int
    recorded_walk_count: int
    observed_walk_count: int
    moving_distance_m: int | None
    moving_s: int | None
    stop_count: int | None
    stop_s: int | None
    avg_speed_mps: float | None
    exclusions: list[tuple[str, int]]
    sources: list[WalkSourceRef]
    window_basis: Literal["walk_end"]
    pending_walk_count: int
    status: Literal["PENDING", "READY"]


class TerritoryStatistics(BaseModel):
    statistics_version: str
    generation_id: str
    coverage_start_ms: int
    confirmed_through_ms: int
    acquisition_count: int
    takeover_count: int
    held_site_ms: int
    verified_held_site_ms: int
    owned_site_count: int
    peak_owned_site_count: int


class ScoreResponse(BaseModel):
    bonus: int
    holding_units: int
    held_site_ms: int
    current_count: int
    scoring_count: int
    peak: int
    claims: int
    takeovers: int
    last_ms: int


class HoldingSourceRef(BaseModel):
    period_id: uuid.UUID
    site_id: str
    claim_id: uuid.UUID | None
    game_session_id: uuid.UUID | None


class TerritorySummaryResponse(BaseModel):
    season_id: str
    pet_id: uuid.UUID
    status: Literal["PENDING", "READY", "STALE"]
    source_revision: int = 0
    processed_revision: int = 0
    statistics: TerritoryStatistics | None
    score: ScoreResponse | None
    score_as_of_ms: int | None = None
    sources: list[HoldingSourceRef]
