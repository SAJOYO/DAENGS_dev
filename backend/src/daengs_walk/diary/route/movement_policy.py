"""Relative pace and scene binding policy values; no calculation imports."""

from typing import Literal

from pydantic import Field

from daengs_walk.diary.contracts.input import DiaryContract


class MovementPolicy(DiaryContract):
    version: Literal["diary-movement-v1"] = "diary-movement-v1"
    slow_ratio: float = Field(default=0.5, gt=0, lt=1)
    fast_ratio: float = Field(default=1.5, gt=1)
    minimum_seconds: float = Field(default=10, gt=0)
    # Initial baseline: median of at least five canonical segments >= 0.5 m/s.
    baseline: Literal["session-median-ge-0.5-min-5-v1"] = "session-median-ge-0.5-min-5-v1"
    binding: Literal["scene-time-midpoints-v1"] = "scene-time-midpoints-v1"
