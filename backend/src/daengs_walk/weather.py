"""One historical grid temperature, bound to the point/time actually queried."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from daengs_walk.value_contracts import Instant, Point, ValueContract


class GridTemperature(ValueContract):
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
