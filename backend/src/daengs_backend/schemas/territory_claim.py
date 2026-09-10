"""Online claim contracts. Session participants and claiming dog are separate."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

SiteId = Annotated[str, Field(max_length=96, pattern=r"^territory-site:hex-v1:140:-?\d+:-?\d+$")]
Phase = Literal["RECORDING", "PAUSED", "ENDED"]


class SessionStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    started_at: AwareDatetime
    pet_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)

    @field_validator("pet_ids")
    @classmethod
    def unique_pets(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("duplicate_pet")
        return sorted(value)


class SessionPhase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: Phase
    expected_version: int = Field(ge=0)


class SessionResponse(BaseModel):
    client_session_id: uuid.UUID
    started_at: datetime
    pet_ids: list[uuid.UUID]
    phase: Phase
    version: int


class MarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_session_id: uuid.UUID
    site_id: SiteId
    claiming_pet_id: uuid.UUID
    observed_at: AwareDatetime
    lat: Decimal = Field(ge=-90, le=90, max_digits=9, decimal_places=7)
    lng: Decimal = Field(ge=-180, le=180, max_digits=10, decimal_places=7)
    accuracy_m: float = Field(ge=0, allow_inf_nan=False)
    is_mock: bool = False


class OccupancyResponse(BaseModel):
    owner_pet_id: uuid.UUID
    owner_pet_name: str
    is_mine: bool
    certification: Literal["UNVERIFIED", "VERIFIED"]
    occupied_at: datetime
    certified_at: datetime | None = None
    protected_until: datetime | None = None
    expires_at: datetime | None = None


class SiteResponse(BaseModel):
    server_now: datetime | None = None
    season_id: str | None = None
    policy_version: str | None = None
    site_id: str
    version: int
    occupancy: OccupancyResponse | None


class ClaimResponse(BaseModel):
    claim_id: uuid.UUID
    client_session_id: uuid.UUID
    claiming_pet_id: uuid.UUID
    disposition: Literal["GRANTED", "PHOTO_REQUIRED", "POLICY_UNDECIDED", "ALREADY_OWNED"]
    photo_status: Literal["NOT_SUBMITTED", "PENDING", "VERIFIED", "REJECTED", "RETRY_PENDING"]
    current_photo_id: uuid.UUID | None
    resolution_code: str | None
    site: SiteResponse


class ChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_site_version: int = Field(ge=0)


class PhotoAccessResponse(BaseModel):
    server_now: datetime
    site_version: int
    season_id: str | None
    policy_version: str | None
    allowed_action: Literal[
        "PHOTO_TAKEOVER", "PHOTO_UPGRADE", "PHOTO_RENEW", "WAIT", "ALREADY_CERTIFIED", "UNAVAILABLE"
    ]
    reason: str | None = None
    protected_until: datetime | None = None


class RenewalRequest(MarkRequest):
    expected_site_version: int = Field(ge=0)


class RenewalResponse(BaseModel):
    renewal_id: uuid.UUID
    site_version: int
    expires_at: datetime
