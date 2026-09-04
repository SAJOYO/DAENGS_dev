from copy import deepcopy
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from daengs_journey.providers.base import Mode

Sort = Literal["distance", "duration", "open_first"]
Urgency = Literal["normal", "urgent"]
TimeKind = Literal["depart_at", "arrive_by", "service_at"]
CURRENT_STATE_VERSION = 4
MAX_HISTORY = 10
PositiveId = Annotated[int, Field(ge=1)]
ShortTag = Annotated[str, Field(min_length=1, max_length=64)]
SymptomText = Annotated[str, Field(min_length=1, max_length=200)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TimeIntent(ContractModel):
    kind: TimeKind
    at: datetime

    @field_validator("at")
    @classmethod
    def timezone_is_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("time intent must include a timezone")
        return value


class TargetPrefs(ContractModel):
    """Round-tripped search state; Journey accepts it but never uses it for routing."""

    radius_m: int = Field(2000, ge=100, le=20000)
    open_now: bool = False
    night_service: bool = False
    emergency_service: bool = False
    symptoms: list[SymptomText] = Field(default_factory=list, max_length=20)
    require_tags: list[ShortTag] = Field(default_factory=list, max_length=20)
    exclude_ids: list[PositiveId] = Field(default_factory=list, max_length=100)
    pin_ids: list[PositiveId] = Field(default_factory=list, max_length=100)
    limit: int = Field(20, ge=1, le=100)


class WalkPrefs(ContractModel):
    max_walk_min: int | None = Field(None, ge=1, le=1440)


class JourneyPrefs(ContractModel):
    preferred_mode: Mode | None = None
    max_total_min: int | None = Field(None, ge=1, le=1440)
    hard_limit: bool = False
    walk: WalkPrefs = Field(default_factory=WalkPrefs)


class EditableState(ContractModel):
    """DAENGS_geo state v4 wire contract, without importing its search implementation."""

    state_version: Literal[CURRENT_STATE_VERSION] = CURRENT_STATE_VERSION
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    time_intent: TimeIntent | None = None
    urgency: Urgency | None = None
    target: TargetPrefs = Field(default_factory=TargetPrefs)
    journey: JourneyPrefs = Field(default_factory=JourneyPrefs)
    sort: Sort = "distance"
    history: list[dict] = Field(default_factory=list, max_length=MAX_HISTORY)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_state(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = deepcopy(value)
        version = data.get("state_version", 1)
        if version not in (1, 2, 3, CURRENT_STATE_VERSION):
            raise ValueError(f"unsupported state_version: {version}")

        target = data.get("target")
        if isinstance(target, dict):
            if "night" in target:
                target.setdefault("night_service", target.pop("night"))
            if "emergency" in target:
                target.setdefault("emergency_service", target.pop("emergency"))
            target.pop("specialty", None)
            legacy_at = target.pop("at", None)
            if legacy_at is not None and data.get("time_intent") is None:
                data["time_intent"] = {"kind": "service_at", "at": legacy_at}

        journey = data.get("journey")
        if isinstance(journey, dict) and isinstance(journey.get("walk"), dict):
            journey["walk"].pop("option", None)
            journey["walk"].pop("avoid", None)
        data["state_version"] = CURRENT_STATE_VERSION
        return data

    @field_validator("history")
    @classmethod
    def validate_history_snapshots(cls, value: list[dict]) -> list[dict]:
        normalized: list[dict] = []
        for snapshot in value:
            candidate = dict(snapshot)
            candidate["history"] = []
            normalized.append(cls.model_validate(candidate).snapshot())
        return normalized

    def snapshot(self) -> dict:
        return self.model_dump(exclude={"history"})
