"""Private calculation/selection contracts. Never dump these into a writer request."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Scope = Literal["reference_point", "object_boundary", "linear_feature"]
Case = Literal[
    "distance_decrease",
    "distance_increase",
    "distance_valley",
    "distance_stable",
    "alongside",
    "passing",
    "route_retrace",
    "route_return",
    "route_turn",
    "route_straight",
]


@dataclass(frozen=True)
class DistanceSample:
    at: datetime
    distance_m: float
    accuracy_m: float
    source_id: str
    continuity_id: int = 0
    path_offset_m: float | None = None
    target_axis_offset_m: float | None = None


@dataclass(frozen=True)
class DistanceTrack:
    target_id: str
    name: str
    scope: Scope
    samples: tuple[DistanceSample, ...]
    source_version: str

    def __post_init__(self):
        if self.scope not in {"reference_point", "object_boundary", "linear_feature"}:
            raise ValueError("unsupported distance scope")


@dataclass(frozen=True)
class FlowPolicy:
    version: str = "relation-flow-analysis-v1"
    minimum_change_m: float = 20
    max_gap_seconds: float = 30
    minimum_duration_s: float = 10
    alongside_limit_m: float = 50

    def __post_init__(self):
        from math import isfinite

        if any(
            not isfinite(v) or v <= 0
            for v in (
                self.minimum_change_m,
                self.max_gap_seconds,
                self.minimum_duration_s,
                self.alongside_limit_m,
            )
        ):
            raise ValueError("invalid relation calculation threshold")


@dataclass(frozen=True)
class RelationFlow:
    id: str
    case: Case
    started_at: datetime
    ended_at: datetime
    target_id: str | None
    target_name: str | None
    target_scope: Scope | None
    profile: tuple[DistanceSample, ...]
    source_ids: tuple[str, ...]
    calculation_policy: str
    source_version: str
    interval_view: dict | None = None


@dataclass(frozen=True)
class RelationSelection:
    """An explicit allowlist, with no words or required narrative focus."""

    scene_id: str
    flows: tuple[RelationFlow, ...]
    spatial_relation_ids: tuple[str, ...]
    replaced_relation_ids: tuple[str, ...]
    policy: str = "relation-injection-v1"
