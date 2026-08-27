"""종류별 Place 발견을 조율하는 canonical 검색 서비스.

각 resolver는 자기 원천의 존재·병합 규칙을 유지한다. 이 계층은 요청한 kind를 알맞은
resolver로 보내고 공통 `PlaceResult`로 바꾼 뒤, 종류 안에서만 요청한 사실 선호를 적용한다.
"""

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import SystemClock
from app.geo.ranking import DISTANCE_BAND_M, prefer_boost, rank_key
from app.place.adapters import facility_place_result, medical_place_result
from app.place.contracts import PlaceResult
from app.place.evaluations import DogAccessEvaluation, evaluate_dog_access
from app.place.facility_resolver import (
    MAX_RESULTS,
    FacilityParams,
    resolve_facilities,
)
from app.place.medical_resolver import resolve_medical_places
from app.place.source_catalog import KCISA_KINDS, KTO_KINDS, MOIS_SOURCES
from app.profile.contract import SizeClass
from app.profile.source import profile_source


class PlaceKind(StrEnum):
    HOSPITAL = "hospital"
    PHARMACY = "pharmacy"
    PET_SHOP = "pet_shop"
    SHOPPING = "shopping"
    GROOMING = "grooming"
    BOARDING = "boarding"
    TRAVEL = "travel"
    LEISURE = "leisure"
    MUSEUM = "museum"
    GALLERY = "gallery"
    ARTS_CENTER = "arts_center"
    CULTURE = "culture"
    CAFE = "cafe"
    RESTAURANT = "restaurant"
    PENSION = "pension"
    HOTEL = "hotel"
    STAY = "stay"
    ETC = "etc"


MEDICAL_KINDS = frozenset({PlaceKind.HOSPITAL, PlaceKind.PHARMACY})
PLACE_KINDS = frozenset(PlaceKind)
MAX_KINDS_PER_REQUEST = 6
MAX_TOTAL_RESULTS = 5000

_RESOLVER_KINDS = {
    *KCISA_KINDS.values(),
    *KTO_KINDS.values(),
    *(source.kind for source in MOIS_SOURCES.values()),
    "etc",
}
_DECLARED_KINDS = {kind.value for kind in PLACE_KINDS}
if _DECLARED_KINDS != _RESOLVER_KINDS:
    missing = sorted(_RESOLVER_KINDS - _DECLARED_KINDS)
    retired = sorted(_DECLARED_KINDS - _RESOLVER_KINDS)
    raise RuntimeError(
        "PlaceKind and resolver mappings differ: "
        f"missing={missing}, without_resolver={retired}"
    )


class PlaceSearchConditions(BaseModel):
    """사용자가 명시한 대조 조건. 장소를 제거하거나 순서를 바꾸지 않는다."""

    dog_id: str | None = Field(None, min_length=1, max_length=128)
    dog_size: SizeClass | None = None
    dog_weight_kg: float | None = Field(None, gt=0, le=200)

    @model_validator(mode="after")
    def require_a_dog_subject(self) -> Self:
        if self.dog_id is None and self.dog_size is None:
            raise ValueError("conditions require dog_id or dog_size")
        return self


class AppliedPlaceSearchConditions(BaseModel):
    """실제 평가에 사용한 조건. 프로필을 못 읽으면 dog_size는 미상으로 남는다."""

    dog_id: str | None = None
    dog_size: SizeClass | None = None
    dog_weight_kg: float | None = Field(None, gt=0, le=200)


class PlaceSearchPreferences(BaseModel):
    """사용자가 명시한 사실 기반 선호. 결과를 제거하지 않는다."""

    model_config = ConfigDict(extra="forbid")

    parking: bool = False


class PlaceSearchRequest(BaseModel):
    lat: float = Field(ge=32, le=40)
    lng: float = Field(ge=123, le=133)
    radius_m: int = Field(3000, ge=100, le=20000)
    kinds: list[PlaceKind] = Field(min_length=1, max_length=MAX_KINDS_PER_REQUEST)
    # 지도는 반경 안 후보를 가능한 한 보되 서버 상한은 명시한다. 잘렸는지는 group이 말한다.
    limit_per_kind: int | None = Field(None, ge=1, le=MAX_RESULTS)
    conditions: PlaceSearchConditions | None = None
    preferences: PlaceSearchPreferences | None = None

    @field_validator("kinds", mode="before")
    @classmethod
    def reject_unknown_kinds(cls, values):
        if not isinstance(values, list):
            return values
        if "goods" in values:
            raise ValueError("goods was split into pet_shop and shopping")
        known = {kind.value for kind in PLACE_KINDS}
        unknown = sorted({value for value in values if isinstance(value, str)} - known)
        if unknown:
            raise ValueError(f"unknown place kinds: {', '.join(unknown)}")
        return values

    @field_validator("kinds")
    @classmethod
    def require_unique_kinds(cls, values: list[PlaceKind]) -> list[PlaceKind]:
        if len(set(values)) != len(values):
            raise ValueError("kinds must be unique")
        return values

    @model_validator(mode="after")
    def stay_inside_request_budget(self) -> Self:
        if (
            self.limit_per_kind is not None
            and self.limit_per_kind * len(self.kinds) > MAX_TOTAL_RESULTS
        ):
            raise ValueError(
                f"limit_per_kind across all kinds must not exceed {MAX_TOTAL_RESULTS} results"
            )
        return self

    @property
    def effective_limit_per_kind(self) -> int:
        if self.limit_per_kind is not None:
            return self.limit_per_kind
        return min(MAX_RESULTS, MAX_TOTAL_RESULTS // len(self.kinds))


class BooleanFactCoverage(BaseModel):
    """반환된 그룹 안의 3상태 불 사실 개수. unknown은 false가 아니다."""

    known_true: int = Field(ge=0)
    known_false: int = Field(ge=0)
    unknown: int = Field(ge=0)


class PlaceSort(BaseModel):
    type: Literal["distance", "distance_preferred"] = "distance"
    basis: tuple[Literal["distance_band", "parking", "distance_m"], ...] = (
        "distance_m",
    )
    applied: tuple[Literal["parking"], ...] = Field(
        default=(), exclude_if=lambda value: not value,
    )
    band_m: int | None = Field(None, ge=1, exclude_if=lambda value: value is None)
    coverage: dict[Literal["parking"], BooleanFactCoverage] = Field(
        default_factory=dict, exclude_if=lambda value: not value,
    )


class PlaceEvaluations(BaseModel):
    dog_access: DogAccessEvaluation | None = Field(
        None, exclude_if=lambda value: value is None,
    )


class PlaceSearchHit(BaseModel):
    place: PlaceResult
    evaluations: PlaceEvaluations = Field(default_factory=PlaceEvaluations)


class PlaceSearchGroup(BaseModel):
    kind: PlaceKind
    sort: PlaceSort = Field(default_factory=PlaceSort)
    limit: int = Field(ge=1)
    truncated: bool = False
    results: list[PlaceSearchHit]


class PlaceSearchResponse(BaseModel):
    conditions: AppliedPlaceSearchConditions | None = Field(
        None, exclude_if=lambda value: value is None,
    )
    groups: list[PlaceSearchGroup]


def _distance_key(result: PlaceResult) -> tuple[int, str, str]:
    return result.distance_m, result.key.source, result.key.ref


def _parking_preference_key(result: PlaceResult) -> tuple:
    hit = ("parking",) if result.facts.parking is True else ()
    return (
        *rank_key(
            primary=result.distance_m,
            boost=prefer_boost(hit),
            band_size=DISTANCE_BAND_M,
        ),
        result.key.source,
        result.key.ref,
    )


def _parking_coverage(results: list[PlaceResult]) -> BooleanFactCoverage:
    return BooleanFactCoverage(
        known_true=sum(result.facts.parking is True for result in results),
        known_false=sum(result.facts.parking is False for result in results),
        unknown=sum(result.facts.parking is None for result in results),
    )


def _hit(
    result: PlaceResult,
    conditions: AppliedPlaceSearchConditions | None,
) -> PlaceSearchHit:
    dog_access = None
    if conditions is not None and result.match.kind not in MEDICAL_KINDS:
        dog_access = evaluate_dog_access(
            result.facts.pet_access,
            conditions.dog_size,
            conditions.dog_weight_kg,
        )
    return PlaceSearchHit(
        place=result,
        evaluations=PlaceEvaluations(dog_access=dog_access),
    )


async def _medical_group(
    db: AsyncSession,
    request: PlaceSearchRequest,
    kind: PlaceKind,
    limit: int,
    conditions: AppliedPlaceSearchConditions | None,
) -> PlaceSearchGroup:
    rows = await resolve_medical_places(
        db,
        lat=request.lat,
        lng=request.lng,
        radius_m=request.radius_m,
        judge_at=SystemClock().now(),
        kind=kind.value,
        limit=limit + 1,
    )
    truncated = len(rows) > limit
    places = sorted((medical_place_result(row) for row in rows), key=_distance_key)[:limit]
    results = [_hit(place, conditions) for place in places]
    return PlaceSearchGroup(kind=kind, limit=limit, truncated=truncated, results=results)


async def _facility_group(
    db: AsyncSession,
    request: PlaceSearchRequest,
    kind: PlaceKind,
    limit: int,
    conditions: AppliedPlaceSearchConditions | None,
) -> PlaceSearchGroup:
    prefer_parking = bool(request.preferences and request.preferences.parking)
    resolved = await resolve_facilities(
        FacilityParams(
            lat=request.lat,
            lng=request.lng,
            radius_m=request.radius_m,
            kind=kind.value,
            limit=limit,
            only_dog_ok=False,
            parking=prefer_parking,
        ),
        db,
        require_canonical_identity=True,
    )
    places = [facility_place_result(row) for row in resolved.results]
    sort = PlaceSort()
    if prefer_parking:
        places.sort(key=_parking_preference_key)
        sort = PlaceSort(
            type="distance_preferred",
            basis=("distance_band", "parking", "distance_m"),
            applied=("parking",),
            band_m=DISTANCE_BAND_M,
            coverage={"parking": _parking_coverage(places)},
        )
    else:
        places.sort(key=_distance_key)
    results = [_hit(place, conditions) for place in places]
    return PlaceSearchGroup(
        kind=kind,
        sort=sort,
        limit=limit,
        truncated=resolved.truncated,
        results=results,
    )


async def _resolve_conditions(
    conditions: PlaceSearchConditions | None,
) -> AppliedPlaceSearchConditions | None:
    if conditions is None:
        return None
    dog_size = conditions.dog_size
    dog_weight_kg = conditions.dog_weight_kg
    # dog_size 명시는 다른 개를 뜻할 수 있다. 그때 기존 프로필 무게를 조용히 섞지 않는다.
    if dog_size is None and conditions.dog_id is not None:
        profile = await profile_source().get(conditions.dog_id)
        if profile is not None:
            dog_size = profile.size_class
            if dog_weight_kg is None:
                dog_weight_kg = profile.weight_kg
    return AppliedPlaceSearchConditions(
        dog_id=conditions.dog_id,
        dog_size=dog_size,
        dog_weight_kg=dog_weight_kg,
    )


async def search_place_groups(
    db: AsyncSession,
    request: PlaceSearchRequest,
) -> PlaceSearchResponse:
    """요청 kind 순서를 보존하고, 서로 다른 kind 사이에는 순위를 만들지 않는다."""
    limit = request.effective_limit_per_kind
    conditions = await _resolve_conditions(request.conditions)
    groups: list[PlaceSearchGroup] = []
    for kind in request.kinds:
        if kind in MEDICAL_KINDS:
            group = await _medical_group(db, request, kind, limit, conditions)
        else:
            group = await _facility_group(db, request, kind, limit, conditions)
        groups.append(group)
    return PlaceSearchResponse(conditions=conditions, groups=groups)
