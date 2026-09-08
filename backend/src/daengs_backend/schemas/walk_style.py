"""지도 표시 정책. 기록·측정값과 분리된, 기기 색상 선택용 계약."""

from typing import Literal

from pydantic import BaseModel, Field


class WalkColorTheme(BaseModel):
    id: Literal["pink", "red", "blue", "purple"]
    label: str
    colors: list[str] = Field(min_length=5, max_length=5)


class WalkStylePolicy(BaseModel):
    version: Literal[1] = 1
    default_theme: Literal["pink"] = "pink"
    speed_max_mps: float = 2.0
    boundaries_mps: list[float] = Field(default_factory=lambda: [0.4, 0.8, 1.2, 1.6])
    blend_half_width_mps: float = 0.05
    unknown_color: str = "#8a8a8a"
    themes: list[WalkColorTheme]
