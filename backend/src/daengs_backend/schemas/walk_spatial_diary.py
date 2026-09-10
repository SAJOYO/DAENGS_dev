"""앱이 요청하고 받는 Walk 공간 일기 View HTTP 계약."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from daengs_walk.spatial_diary import (
    CONTEXT_FACET_POLICY_VERSION,
    SPATIAL_AGGREGATION_VERSION,
    SPATIAL_DIARY_VIEW_VERSION,
    SpatialDiaryViewSpec,
    SpatialFieldMetric,
    WalkSelector,
)


class FrozenApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SpatialDiaryViewRequest(FrozenApiModel):
    """순수 ViewSpec과 같은 모양이지만 HTTP 경계에서 별도로 고정한 요청."""

    view_version: Literal[SPATIAL_DIARY_VIEW_VERSION] = SPATIAL_DIARY_VIEW_VERSION
    walk_selector: WalkSelector
    field_metric: SpatialFieldMetric

    def to_spec(self) -> SpatialDiaryViewSpec:
        return SpatialDiaryViewSpec(
            view_version=self.view_version,
            walk_selector=self.walk_selector,
            field_metric=self.field_metric,
        )


class SpatialDiaryProjectionResponse(FrozenApiModel):
    paint_version: int
    grid_version: str
    radius_u: float
    profile_name: str
    profile_fp: str
    sample_step_m: float
    paint_fp: str


class SpatialDiaryFieldCellResponse(FrozenApiModel):
    q: int
    r: int
    value: float = Field(ge=0, allow_inf_nan=False)
    numerator: float = Field(ge=0, allow_inf_nan=False)


class SpatialDiaryFieldResponse(FrozenApiModel):
    metric: SpatialFieldMetric
    unit: str
    normalization: str
    denominator: float = Field(ge=0, allow_inf_nan=False)
    cells: tuple[SpatialDiaryFieldCellResponse, ...]


class SpatialDiaryReceiptResponse(FrozenApiModel):
    selector_fingerprint: str
    view_as_of: datetime
    total_capsules: int = Field(ge=0)
    selected_capsules: int = Field(ge=0)
    contributing_capsules: int = Field(ge=0)
    context_known_count: int = Field(ge=0)
    context_unknown_count: int = Field(ge=0)
    paint_fp: str
    field_metric: SpatialFieldMetric
    normalization: str
    context_policy_version: Literal[CONTEXT_FACET_POLICY_VERSION]
    aggregation_version: Literal[SPATIAL_AGGREGATION_VERSION]


class SpatialDiaryViewResponse(FrozenApiModel):
    spec: SpatialDiaryViewRequest
    projection: SpatialDiaryProjectionResponse
    field: SpatialDiaryFieldResponse
    receipt: SpatialDiaryReceiptResponse


class WalkRecordSheetsRequest(FrozenApiModel):
    client_session_ids: tuple[uuid.UUID, ...] = Field(min_length=1, max_length=400)

    @field_validator("client_session_ids")
    @classmethod
    def unique_ids(cls, value: tuple[uuid.UUID, ...]) -> tuple[uuid.UUID, ...]:
        if len(set(value)) != len(value):
            raise ValueError("client_session_ids must be unique")
        return value


class WalkRecordSheetResponse(FrozenApiModel):
    client_session_id: uuid.UUID
    walk_id: uuid.UUID | None
    status: Literal["ready", "pending", "unavailable"]
    analysis_id: uuid.UUID | None
    sheet_fingerprint: str | None
    # native Cellophane envelope; service validates its persisted fingerprint first.
    sheet: dict[str, Any] | None


class WalkRecordSheetsResponse(FrozenApiModel):
    schema_version: Literal[1] = 1
    items: tuple[WalkRecordSheetResponse, ...]
