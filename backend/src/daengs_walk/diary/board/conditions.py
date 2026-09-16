"""Card-only weather read model from admitted, scene-bound observations."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from daengs_walk.value_contracts import Instant, ValueContract


class SceneTemperature(ValueContract):
    temperature_c: float = Field(ge=-90, le=60)
    unit: Literal["celsius"] = "celsius"
    provider: Literal["kma-vilage-fcst:ncst"]
    observed_at: Instant
    scene_at: Instant
    grid: tuple[Annotated[int, Field(gt=0)], Annotated[int, Field(gt=0)]]
    evidence_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def past_observation(self):
        if not 0 <= (self.scene_at - self.observed_at).total_seconds() <= 7200:
            raise ValueError("card temperature is outside the scene observation window")
        return self


def scene_temperature(stamp, anchor):
    item = stamp.temperature_reference
    if item is None:
        return None
    return SceneTemperature(
        temperature_c=item.facts["temperature_c"],
        provider=item.facts["provider"],
        observed_at=item.facts["observed_at"],
        scene_at=anchor.event_at,
        grid=item.facts["grid"],
        evidence_id=item.id,
    )
