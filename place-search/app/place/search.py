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
from app.place.contracts import DogSize, PlaceResult
from app.place.evaluations import DogAccessEvaluation, evaluate_dog_access
from app.place.facility_resolver import (
    MAX_RESULTS,
    FacilityParams,
    resolve_facilities,
)
from app.place.medical_resolver import resolve_medical_places
from app.place.restriction_projection import DogRestrictionEvaluation, project
from app.place.source_catalog import KCISA_KINDS, KTO_KINDS, MOIS_SOURCES


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
    """사용자가 명시한 대조 조건. 장소를 제거하거나 순서를 바꾸지 않는다.

    **identity 가 아니라 값만 받는다.** dog_id → 크기·무게·나이 projection 은 프로필
    소유자(호출자 쪽 게이트웨이)의 일이다 — 이 레포는 프로필을 소유하지 않는다
    (docs/contracts/dog-profile.md). 준 값을 그대로 평가에 쓰고 응답에 그대로 되돌리므로
    "무엇을 기준으로 대조했나"가 항상 요청과 일치한다.

    모르는 키는 422 다 — `preferences` 와 같은 이유(extra="forbid")다. 판정 의미가 있는
    입력에서 오타(`dog_weigth_kg`)나 옛 계약(`dog_id`)을 조용히 무시하면 덜 개인화된
    결과가 정상 응답처럼 나간다. 계약이 틀렸다고 즉시 말하는 쪽이 낫다.
    """

    model_config = ConfigDict(extra="forbid")

    dog_size: DogSize | None = None
    dog_weight_kg: float | None = Field(None, gt=0, le=200)
    # `deny:age` 술어를 대조하는 유일한 재료. 나이도 프로필의 사실이므로 견주가 매번
    # 적는 값이 아니라 호출자가 프로필에서 계산해 보내는 값이다.
    dog_age_years: float | None = Field(None, ge=0, le=40)

    @model_validator(mode="after")
    def require_a_dog_subject(self) -> Self:
        if self.dog_size is None and self.dog_weight_kg is None and self.dog_age_years is None:
            raise ValueError(
                "conditions require at least one of dog_size, dog_weight_kg, dog_age_years"
            )
        return self


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


class RestrictionCoverage(BaseModel):
    """이 그룹에서 동반 조건을 **얼마나 아는가.** 사실 상태별 커버리지.

    칩이 0개인 이유가 셋이고 사용자가 할 행동이 다르므로, 개수도 셋으로 센다.
    이게 없으면 "이 동네는 조건 없는 곳뿐" 과 "정보가 없는 원천층" 이 같은 화면이 된다 —
    `BooleanFactCoverage` 를 파킹에 둔 것과 같은 이유다 (결정 #70 §6).
    """

    none_confirmed: int = Field(0, ge=0)
    not_applicable: int = Field(0, ge=0)
    restricted: int = Field(0, ge=0)
    unknown: int = Field(0, ge=0)
    # 술어가 원문을 다 담지 못한 행. UI 가 원문 보기를 강제해야 하는 수다.
    needs_raw: int = Field(0, ge=0)


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
    # 조건 술어를 이 개에 대고 본 결과. `dog_access`(원천 크기 축)와 **다른 축**이라
    # 합치지 않는다 — 원천이 두 곳에 다르게 적었다는 사실 자체가 정보다.
    restrictions: DogRestrictionEvaluation | None = Field(
        None, exclude_if=lambda value: value is None,
    )


class PlaceSearchHit(BaseModel):
    place: PlaceResult
    evaluations: PlaceEvaluations = Field(default_factory=PlaceEvaluations)


class PlaceSearchGroup(BaseModel):
    kind: PlaceKind
    sort: PlaceSort = Field(default_factory=PlaceSort)
    # 의료 그룹에는 없다 — `restrictions` 는 시설 원천(KCISA)만 가진 사실이다.
    restrictions: RestrictionCoverage | None = Field(
        None, exclude_if=lambda value: value is None,
    )
    limit: int = Field(ge=1)
    truncated: bool = False
    results: list[PlaceSearchHit]


class PlaceSearchResponse(BaseModel):
    # 평가에 쓴 조건의 에코. 서버가 값을 보정하지 않으므로 요청의 conditions 와 같다 —
    # 그래도 되돌리는 이유는 "무엇을 기준으로 대조했나"를 응답만 보고 알 수 있어야 해서다.
    conditions: PlaceSearchConditions | None = Field(
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


def _restriction_coverage(results: list[PlaceResult]) -> RestrictionCoverage:
    """그룹 안 제약 상태 분포. **미상과 '제한 없음' 을 합치지 않는다.**"""
    coverage = RestrictionCoverage()
    for result in results:
        facts = result.facts.restrictions
        state = facts.state if facts is not None else "unknown"
        if state == "none_confirmed":
            coverage.none_confirmed += 1
        elif state == "not_applicable":
            coverage.not_applicable += 1
        elif state == "restricted":
            coverage.restricted += 1
        else:
            coverage.unknown += 1
        if facts is not None and facts.parse_state in ("partial", "raw_only"):
            coverage.needs_raw += 1
    return coverage


def _hit(
    result: PlaceResult,
    conditions: PlaceSearchConditions | None,
) -> PlaceSearchHit:
    dog_access = None
    restrictions = None
    if conditions is not None and result.match.kind not in MEDICAL_KINDS:
        dog_access = evaluate_dog_access(
            result.facts.pet_access,
            conditions.dog_size,
            conditions.dog_weight_kg,
        )
        restrictions = project(
            result.facts.restrictions,
            dog_size=conditions.dog_size,
            dog_age_years=conditions.dog_age_years,
        )
        if "facts.restrictions" in result.field_sources:
            # 현재 facility_link는 name+150m의 미검증 연결이다. 다른 레코드의 조건을
            # 표시하는 것은 가능하지만, 그 조건으로 이 장소를 자동 불가/가능 판정하면
            # identity 신뢰도보다 강한 결론이 된다. 칩은 남기고 판정만 fail closed 한다.
            restrictions = DogRestrictionEvaluation(
                state="unknown",
                reason="unverified_source_match",
                chips=restrictions.chips,
            )
    return PlaceSearchHit(
        place=result,
        evaluations=PlaceEvaluations(
            dog_access=dog_access, restrictions=restrictions,
        ),
    )


async def _medical_group(
    db: AsyncSession,
    request: PlaceSearchRequest,
    kind: PlaceKind,
    limit: int,
    conditions: PlaceSearchConditions | None,
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
    conditions: PlaceSearchConditions | None,
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
        restrictions=_restriction_coverage(places),
    )


async def search_place_groups(
    db: AsyncSession,
    request: PlaceSearchRequest,
) -> PlaceSearchResponse:
    """요청 kind 순서를 보존하고, 서로 다른 kind 사이에는 순위를 만들지 않는다."""
    limit = request.effective_limit_per_kind
    conditions = request.conditions
    groups: list[PlaceSearchGroup] = []
    for kind in request.kinds:
        if kind in MEDICAL_KINDS:
            group = await _medical_group(db, request, kind, limit, conditions)
        else:
            group = await _facility_group(db, request, kind, limit, conditions)
        groups.append(group)
    return PlaceSearchResponse(conditions=conditions, groups=groups)
