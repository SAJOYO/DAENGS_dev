from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.providers.base import Mode

Companion = Literal["dog", "none"]


@dataclass(frozen=True)
class WalkPlan:
    max_walk_min: int | None = None


@dataclass(frozen=True)
class JourneyPlan:
    """Execution plan for the profile-free request currently sent by DAENGS_APP."""

    origin_lat: float
    origin_lng: float
    resolved_at: datetime
    departure_at: datetime
    companion: Companion = "dog"
    measured: bool = False
    mode_priority: tuple[Mode, ...] = field(default_factory=tuple)
    max_total_min: int | None = None
    hard_limit: bool = False
    walk: WalkPlan = field(default_factory=WalkPlan)
