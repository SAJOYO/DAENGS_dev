"""좌표·과거 시각 환경 조회의 공개 계약."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

WeatherAtStatusName = Literal["captured", "partial", "unknown", "failed"]
WeatherRepresentation = Literal["number", "code", "interval"]
WeatherSourceOutcomeName = Literal["ok", "no_data", "error"]


def _timezone_required(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("시각은 시간대를 포함해야 합니다.")
    return value


class WeatherAtRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=33.0, le=39.0, description="위도 (WGS84)")
    lon: float = Field(ge=124.0, le=132.0, description="경도 (WGS84)")
    observed_at: datetime

    _observed_at_has_timezone = field_validator("observed_at")(_timezone_required)


class WeatherAtomOut(BaseModel):
    """판단어로 접기 전의 관측 원자 하나."""

    model_config = ConfigDict(extra="forbid")

    quantity: str
    representation: WeatherRepresentation
    value: float | str
    unit: str | None = None
    raw_value: str | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    source: str
    spatial_ref: str
    valid_at: datetime
    issued_at: datetime

    _valid_at_has_timezone = field_validator("valid_at")(_timezone_required)
    _issued_at_has_timezone = field_validator("issued_at")(_timezone_required)


class WeatherSourceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    outcome: WeatherSourceOutcomeName
    reason: str | None = None
    calls: int = Field(ge=0)


class WeatherAtOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: WeatherAtStatusName
    requested_at: datetime
    fetched_at: datetime
    grid: tuple[int, int]
    observations: list[WeatherAtomOut] = Field(default_factory=list)
    sources: list[WeatherSourceOut] = Field(default_factory=list)

    _requested_at_has_timezone = field_validator("requested_at")(_timezone_required)
    _fetched_at_has_timezone = field_validator("fetched_at")(_timezone_required)


__all__ = [
    "WeatherAtOut",
    "WeatherAtRequest",
    "WeatherAtomOut",
    "WeatherSourceOut",
]
