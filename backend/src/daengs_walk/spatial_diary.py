"""봉인된 Cellophane을 조건별 공간 일기 field로 읽는 순수 계약.

DB에서 어떤 Capsule을 가져올지와 HTTP 모양은 ``daengs_backend``가 맡는다. 이 모듈은
동결된 산책 당시 context를 현재의 필터 facet으로 분류하고, 이미 선택된 Cellophane을
분모가 드러나는 두 metric으로 겹친다. Place·Journey·Pin 구현은 알지 못한다 (D-049).
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from daengs_walk.capsule import ContextStatus, TrailContextSnapshot
from daengs_walk.cellophane import CANONICAL_PAINT_SPEC, Cellophane, PaintSpec
from daengs_walk.contracts import FrozenContract
from daengs_walk.hex_grid import Cell

SPATIAL_DIARY_VIEW_VERSION = 1
CONTEXT_FACET_POLICY_VERSION = 2
SPATIAL_AGGREGATION_VERSION = 1
DIARY_CALENDAR_TIMEZONE = "Asia/Seoul"
MAX_VIEW_RANGE_DAYS = 366

SpatialFieldMetric = Literal["visit_rate", "walk_utilization"]
ContextFacetAxis = Literal["precipitation", "daylight"]
PrecipitationFacet = Literal["rain", "snow", "mixed", "dry", "unknown"]
DaylightFacet = Literal["day", "night", "unknown"]

# WMO weather interpretation codes. 이것은 창문 그림을 고르는 앱의 OutsideWeather 접기와
# 다른 정책이다. 어는 이슬비·어는 비는 기상 현상 그대로 rain에 두고, 정의되지 않은
# 0..99 값은 현재 상태를 추측하지 않고 unknown으로 남긴다.
WMO_DRY_CODES = frozenset({0, 1, 2, 3, 45, 48})
WMO_RAIN_CODES = frozenset(
    {
        51,
        53,
        55,
        56,
        57,
        61,
        63,
        65,
        66,
        67,
        80,
        81,
        82,
        95,
        96,
        99,
    }
)
WMO_SNOW_CODES = frozenset({71, 73, 75, 77, 85, 86})

FACET_VALUES: dict[str, frozenset[str]] = {
    "precipitation": frozenset({"rain", "snow", "mixed", "dry", "unknown"}),
    "daylight": frozenset({"day", "night", "unknown"}),
}
_DIARY_ZONE = ZoneInfo(DIARY_CALENDAR_TIMEZONE)


def _timezone_required(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("spatial diary timestamps must include a timezone")
    return value


class UnsupportedSpatialDiaryViewError(ValueError):
    """현재 View 세대가 모르는 facet이나 metric이다."""


class MixedPaintGenerationError(ValueError):
    """한 field에 서로 다른 격자·붓 계산 세대가 들어왔다."""


class DuplicateWalkInViewError(ValueError):
    """한 산책의 여러 Analysis·강아지 연결이 중복 분모로 들어왔다."""


class ContextFacets(FrozenContract):
    """TrailContext 원자를 일기 필터가 소비하는 현재 정책으로 분류한 값."""

    precipitation: PrecipitationFacet
    daylight: DaylightFacet


class ContextFacetFilter(FrozenContract):
    """한 context 축에서 허용할 값. 서로 다른 축은 AND로 결합한다."""

    axis: ContextFacetAxis
    values: tuple[str, ...] = Field(min_length=1)
    policy_version: Literal[CONTEXT_FACET_POLICY_VERSION] = CONTEXT_FACET_POLICY_VERSION

    @field_validator("values")
    @classmethod
    def values_are_a_canonical_set(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("context facet values must be unique")
        return tuple(sorted(values))

    @model_validator(mode="after")
    def values_belong_to_axis(self) -> Self:
        unknown = set(self.values) - FACET_VALUES[self.axis]
        if unknown:
            raise ValueError(f"unsupported {self.axis} facet values: {sorted(unknown)}")
        return self


class WalkSelector(FrozenContract):
    """어느 강아지의 어떤 산책 Capsule이 지도 배경 분모에 들어가는가."""

    pet_id: uuid.UUID
    since: date | None = None
    until: date | None = None
    context_facets: tuple[ContextFacetFilter, ...] = ()

    @field_validator("context_facets")
    @classmethod
    def facets_have_canonical_order(
        cls,
        facets: tuple[ContextFacetFilter, ...],
    ) -> tuple[ContextFacetFilter, ...]:
        return tuple(sorted(facets, key=lambda facet: facet.axis))

    @model_validator(mode="after")
    def range_and_facets_are_consistent(self) -> Self:
        if self.since is not None and self.until is not None:
            if self.since > self.until:
                raise ValueError("walk selector since cannot follow until")
            inclusive_days = (self.until - self.since).days + 1
            if inclusive_days > MAX_VIEW_RANGE_DAYS:
                raise ValueError(f"spatial diary range cannot exceed {MAX_VIEW_RANGE_DAYS} days")
        axes = [facet.axis for facet in self.context_facets]
        if len(axes) != len(set(axes)):
            raise ValueError("walk selector facet axes must be unique")
        return self


class SpatialDiaryViewSpec(FrozenContract):
    """저장된 지도 snapshot이 아니라 매번 다시 계산할 읽기 명세."""

    view_version: Literal[SPATIAL_DIARY_VIEW_VERSION] = SPATIAL_DIARY_VIEW_VERSION
    walk_selector: WalkSelector
    field_metric: SpatialFieldMetric


@dataclass(frozen=True)
class SpatialField:
    """셀별 값과 그 값을 설명하는 공통 분모·Paint 세대."""

    metric: SpatialFieldMetric
    values: Mapping[Cell, float]
    numerators: Mapping[Cell, float]
    denominator: float
    selected: int
    contributing: int
    paint_spec: PaintSpec
    unit: str
    normalization: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        object.__setattr__(self, "numerators", MappingProxyType(dict(self.numerators)))
        if self.selected < 0 or not 0 <= self.contributing <= self.selected:
            raise ValueError("contributing walks must fit selected walks")
        if not math.isfinite(self.denominator) or self.denominator < 0:
            raise ValueError("field denominator must be finite and non-negative")
        if set(self.values) != set(self.numerators):
            raise ValueError("field values and numerators must describe the same cells")
        numbers = (*self.values.values(), *self.numerators.values())
        if any(not math.isfinite(value) or value < 0 for value in numbers):
            raise ValueError("spatial field values must be finite and non-negative")
        if self.metric == "visit_rate":
            if self.denominator != float(self.selected):
                raise ValueError("visit rate denominator must be selected walks")
            if self.contributing != self.selected:
                raise ValueError("every selected walk contributes to visit rate")
        elif self.metric == "walk_utilization":
            if self.denominator != float(self.contributing):
                raise ValueError("walk utilization denominator must be contributing walks")

    @property
    def paint_fp(self) -> str:
        return self.paint_spec.fingerprint


class SpatialDiaryViewReceipt(FrozenContract):
    """필터와 집계가 어떤 분모·정책으로 만들어졌는지 남기는 영수증."""

    selector_fingerprint: str = Field(min_length=8, max_length=128)
    view_as_of: datetime
    total_capsules: int = Field(ge=0)
    selected_capsules: int = Field(ge=0)
    contributing_capsules: int = Field(ge=0)
    context_known_count: int = Field(ge=0)
    context_unknown_count: int = Field(ge=0)
    paint_fp: str = Field(min_length=1, max_length=128)
    field_metric: SpatialFieldMetric
    normalization: str = Field(min_length=1, max_length=64)
    context_policy_version: Literal[CONTEXT_FACET_POLICY_VERSION] = CONTEXT_FACET_POLICY_VERSION
    aggregation_version: Literal[SPATIAL_AGGREGATION_VERSION] = SPATIAL_AGGREGATION_VERSION

    _view_time_has_timezone = field_validator("view_as_of")(_timezone_required)

    @model_validator(mode="after")
    def denominators_are_consistent(self) -> Self:
        if self.selected_capsules > self.total_capsules:
            raise ValueError("selected capsules cannot exceed total capsules")
        if self.contributing_capsules > self.selected_capsules:
            raise ValueError("contributing capsules cannot exceed selected capsules")
        if self.context_known_count + self.context_unknown_count != self.selected_capsules:
            raise ValueError("known and unknown context must cover selected capsules")
        if self.field_metric == "visit_rate":
            if self.contributing_capsules != self.selected_capsules:
                raise ValueError("every selected capsule contributes to visit rate")
            if self.normalization != "selected_walks":
                raise ValueError("visit rate must use selected_walks normalization")
        elif self.normalization != "equal_contributing_walks":
            raise ValueError("walk utilization must use equal_contributing_walks normalization")
        return self


def context_facets(snapshot: TrailContextSnapshot) -> ContextFacets:
    """없는 원자를 반대 상태로 채우지 않고 ``unknown``으로 남긴다."""

    if snapshot.status not in {ContextStatus.CAPTURED, ContextStatus.PARTIAL}:
        return ContextFacets(precipitation="unknown", daylight="unknown")

    kind = snapshot.precipitation_kind
    if kind == "none":
        precipitation: PrecipitationFacet = "dry"
    elif kind in {"rain", "snow", "mixed"}:
        precipitation = kind
    elif snapshot.weather_code in WMO_RAIN_CODES:
        precipitation = "rain"
    elif snapshot.weather_code in WMO_SNOW_CODES:
        precipitation = "snow"
    elif snapshot.weather_code in WMO_DRY_CODES:
        precipitation = "dry"
    else:
        precipitation = "unknown"

    daylight: DaylightFacet = (
        "unknown" if snapshot.is_day is None else "day" if snapshot.is_day else "night"
    )
    return ContextFacets(precipitation=precipitation, daylight=daylight)


def matches_walk_selector(
    selector: WalkSelector,
    *,
    started_at: datetime,
    context: TrailContextSnapshot,
) -> bool:
    """날짜와 모든 context facet이 맞는지 판단한다. 강아지 소유권은 DB 경계의 몫이다."""

    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("walk selector timestamps must include a timezone")
    local_day = started_at.astimezone(_DIARY_ZONE).date()
    if selector.since is not None and local_day < selector.since:
        return False
    if selector.until is not None and local_day > selector.until:
        return False

    derived = context_facets(context).model_dump()
    return all(derived[facet.axis] in facet.values for facet in selector.context_facets)


def context_is_known(
    snapshot: TrailContextSnapshot,
    selector: WalkSelector,
) -> bool:
    """필터가 사용한 축, 무필터라면 지원하는 모든 축이 알려졌는지 본다."""

    required = {facet.axis for facet in selector.context_facets} or set(FACET_VALUES)
    derived = context_facets(snapshot).model_dump()
    return all(derived[axis] != "unknown" for axis in required)


def selector_fingerprint(spec: SpatialDiaryViewSpec) -> str:
    """요청에 보이지 않는 달력·분류·집계 정책까지 묶은 재현 지문."""

    payload = {
        "spec": spec.model_dump(mode="json"),
        "calendar_timezone": DIARY_CALENDAR_TIMEZONE,
        "context_policy_version": CONTEXT_FACET_POLICY_VERSION,
        "aggregation_version": SPATIAL_AGGREGATION_VERSION,
        "wmo_dry_codes": sorted(WMO_DRY_CODES),
        "wmo_rain_codes": sorted(WMO_RAIN_CODES),
        "wmo_snow_codes": sorted(WMO_SNOW_CODES),
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def aggregate_spatial_field(
    sheets: Iterable[Cellophane],
    metric: SpatialFieldMetric,
    *,
    paint_spec: PaintSpec = CANONICAL_PAINT_SPEC,
) -> SpatialField:
    """이미 선택된 산책 장을 한 field로 겹친다."""

    selected = tuple(sheets)
    walk_ids = [sheet.walk_id for sheet in selected]
    if len(walk_ids) != len(set(walk_ids)):
        raise DuplicateWalkInViewError(
            "one walk cannot appear more than once in a spatial diary field"
        )
    mismatched = sorted(
        {sheet.paint_fp for sheet in selected if sheet.paint_fp != paint_spec.fingerprint}
    )
    if mismatched:
        raise MixedPaintGenerationError(
            "spatial diary field cannot mix paint generations: "
            f"expected={paint_spec.fingerprint}, actual={mismatched}"
        )

    if metric == "visit_rate":
        return _visit_rate_field(selected, paint_spec)
    if metric == "walk_utilization":
        return _walk_utilization_field(selected, paint_spec)
    raise UnsupportedSpatialDiaryViewError(f"unsupported spatial field metric: {metric}")


def build_view_receipt(
    spec: SpatialDiaryViewSpec,
    field: SpatialField,
    *,
    view_as_of: datetime,
    total_capsules: int,
    context_known_count: int,
) -> SpatialDiaryViewReceipt:
    """field의 분모를 다시 적지 않고 일관된 View 영수증으로 승격한다."""

    if spec.field_metric != field.metric:
        raise ValueError("view spec metric and field metric must match")
    return SpatialDiaryViewReceipt(
        selector_fingerprint=selector_fingerprint(spec),
        view_as_of=view_as_of,
        total_capsules=total_capsules,
        selected_capsules=field.selected,
        contributing_capsules=field.contributing,
        context_known_count=context_known_count,
        context_unknown_count=field.selected - context_known_count,
        paint_fp=field.paint_fp,
        field_metric=field.metric,
        normalization=field.normalization,
    )


def _visit_rate_field(
    sheets: tuple[Cellophane, ...],
    paint_spec: PaintSpec,
) -> SpatialField:
    counts: dict[Cell, float] = defaultdict(float)
    for sheet in sheets:
        for cell in sheet.occupancy:
            counts[cell] += 1.0
    denominator = float(len(sheets))
    numerators = dict(sorted(counts.items()))
    values = (
        {cell: count / denominator for cell, count in numerators.items()} if denominator else {}
    )
    return SpatialField(
        metric="visit_rate",
        values=values,
        numerators=numerators,
        denominator=denominator,
        selected=len(sheets),
        contributing=len(sheets),
        paint_spec=paint_spec,
        unit="ratio",
        normalization="selected_walks",
    )


def _walk_utilization_field(
    sheets: tuple[Cellophane, ...],
    paint_spec: PaintSpec,
) -> SpatialField:
    parts: dict[Cell, list[float]] = defaultdict(list)
    contributing = 0
    for sheet in sheets:
        mass = math.fsum(sheet.occupancy.values())
        if mass <= 0:
            continue
        contributing += 1
        for cell, amount in sheet.occupancy.items():
            if amount > 0:
                parts[cell].append(amount / mass)
    summed = {cell: math.fsum(parts[cell]) for cell in sorted(parts)}
    numerators = {cell: value for cell, value in summed.items() if value > 0}
    denominator = float(contributing)
    values = (
        {cell: value / denominator for cell, value in numerators.items()} if denominator else {}
    )
    return SpatialField(
        metric="walk_utilization",
        values=values,
        numerators=numerators,
        denominator=denominator,
        selected=len(sheets),
        contributing=contributing,
        paint_spec=paint_spec,
        unit="share",
        normalization="equal_contributing_walks",
    )
