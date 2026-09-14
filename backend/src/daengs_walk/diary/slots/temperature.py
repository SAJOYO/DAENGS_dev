"""One historical grid temperature, bound to the point/time actually queried."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from daengs_walk.diary.contracts.input import DiaryContract, Instant, Point


class GridTemperature(DiaryContract):
    format: Literal["kma-grid-temperature-v1"] = "kma-grid-temperature-v1"
    provider: Literal["kma-vilage-fcst:ncst"]
    query_point: Point
    requested_at: Instant
    fetched_at: Instant
    grid: tuple[Annotated[int, Field(gt=0)], Annotated[int, Field(gt=0)]]
    observed_at: Instant
    issued_at: Instant
    temperature_c: float = Field(ge=-90, le=60)
    unit: Literal["celsius"] = "celsius"

    @model_validator(mode="after")
    def observation(self):
        if (
            not self.observed_at <= self.requested_at <= self.fetched_at
            or self.issued_at != self.observed_at
            or any((self.observed_at.minute, self.observed_at.second, self.observed_at.microsecond))
        ):
            raise ValueError("temperature requires an hourly past observation, not a forecast")
        return self


def temperature_candidate(saved, anchor, scene_scope, policy, reject):
    from daengs_walk.diary.contracts.input import digest
    from daengs_walk.diary.slots.sources import evidence

    try:
        value = GridTemperature.model_validate(saved.payload)
    except ValueError:
        reject("environment", saved.id, "invalid_grid_temperature", "unknown")
        return None
    if (
        value.query_point != anchor.point
        or saved.query_point != anchor.point
        or value.requested_at != anchor.event_at
        or value.fetched_at != saved.retrieved_at
        or saved.temporal_basis != "source_observation"
    ):
        reject("environment", saved.id, "weather_query_mismatch", "unknown")
        return None
    age = (anchor.event_at - value.observed_at).total_seconds()
    details = {
        "actual": age,
        "limit": policy.weather_max_age_s,
        "unit": "seconds",
        "observed_at": value.observed_at.isoformat(),
        "scene_at": anchor.event_at.isoformat(),
        "grid": list(value.grid),
    }
    if age > policy.weather_max_age_s:
        reject("environment", saved.id, "weather_observation_too_old", **details)
        return None
    return evidence(
        "environment",
        "grid_temperature_observation",
        saved.id,
        digest(saved),
        {"provider": value.provider, "grid": value.grid},
        {
            **value.model_dump(mode="json", exclude={"query_point", "requested_at", "fetched_at"}),
            "retrieved_at": value.fetched_at.isoformat(),
            "observation_age_s": age,
            "interpretation": "기록 시각 이하의 해당 격자 기온 관측. 현장 체감이나 기록 순간의 직접 측정은 아님.",
        },
        (-value.observed_at.timestamp(), -value.fetched_at.timestamp()),
        scope={**scene_scope, "grid": value.grid, "observed_at": value.observed_at.isoformat()},
        diagnostics=details,
    )
