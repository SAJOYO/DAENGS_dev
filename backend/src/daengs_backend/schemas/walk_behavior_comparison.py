"""행동 기록이 있는 산책과 기준 산책의 공간 비교. 행동 확률·성향 추론이 아닙니다."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from daengs_backend.schemas.walk_entry_v2 import EvidenceV2
from daengs_backend.schemas.walk_spatial_diary import (
    FrozenApiModel,
    SpatialDiaryFieldResponse,
    SpatialDiaryProjectionResponse,
)
from daengs_walk.spatial_diary import (
    CONTEXT_FACET_POLICY_VERSION,
    SPATIAL_AGGREGATION_VERSION,
    SpatialDiaryViewSpec,
    WalkSelector,
)

COMPARISON_VERSION = "walk-behavior-comparison-v1"
BehaviorCode = Literal["sniffing", "excretion", "barking"]


class BehaviorComparisonRequest(FrozenApiModel):
    comparison_version: Literal[COMPARISON_VERSION] = COMPARISON_VERSION
    walk_selector: WalkSelector
    behavior_code: BehaviorCode

    def spatial_spec(self) -> SpatialDiaryViewSpec:
        return SpatialDiaryViewSpec(
            walk_selector=self.walk_selector,
            field_metric="walk_utilization",
        )


class BehaviorComparisonCohort(FrozenApiModel):
    walk_ids: tuple[uuid.UUID, ...]
    field: SpatialDiaryFieldResponse


class BehaviorComparisonSummary(FrozenApiModel):
    selected_walk_count: int = Field(ge=0)
    excluded_empty_walk_count: int = Field(ge=0)
    entry_count: int = Field(ge=0)
    recorded_day_count: int = Field(ge=0)
    unlocated_entry_count: int = Field(ge=0)


class BehaviorComparisonEvidence(EvidenceV2):
    client_session_id: uuid.UUID | None


class BehaviorComparisonReceipt(FrozenApiModel):
    source_revision: str
    view_as_of: datetime
    paint_fp: str
    context_policy_version: Literal[CONTEXT_FACET_POLICY_VERSION] = CONTEXT_FACET_POLICY_VERSION
    aggregation_version: Literal[SPATIAL_AGGREGATION_VERSION] = SPATIAL_AGGREGATION_VERSION


class BehaviorComparisonResponse(FrozenApiModel):
    spec: BehaviorComparisonRequest
    projection: SpatialDiaryProjectionResponse
    baseline: BehaviorComparisonCohort
    matching: BehaviorComparisonCohort
    summary: BehaviorComparisonSummary
    evidence: tuple[BehaviorComparisonEvidence, ...]
    receipt: BehaviorComparisonReceipt
