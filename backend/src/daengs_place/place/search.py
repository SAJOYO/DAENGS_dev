"""종류별 Place 발견을 조율하는 canonical 검색 서비스.

각 resolver는 자기 원천의 존재·병합 규칙을 유지한다. 이 계층은 요청한 kind를 알맞은
resolver로 보내고 공통 `PlaceResult`로 바꾼 뒤, 종류 안에서만 요청한 사실 선호를 적용한다.
"""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.core.clock import SystemClock
from daengs_place.geo.ranking import DISTANCE_BAND_M, prefer_boost, rank_key
from daengs_place.place.adapters import facility_place_result, medical_place_result
from daengs_place.place.contracts import DogSize, PlaceResult
from daengs_place.place.evaluations import DogAccessEvaluation, evaluate_dog_access
from daengs_place.place.facility_resolver import (
    MAX_RESULTS,
    FacilityParams,
    resolve_facilities,
)
from daengs_place.place.medical_resolver import resolve_medical_places
from daengs_place.place.name_query import PlaceNameQuery
from daengs_place.place.planning.compiler import build_place_search_plan
from daengs_place.place.planning.contract import (
    MAX_KINDS_PER_REQUEST,
    MAX_TOTAL_RESULTS,
    PlaceKind,
    PlaceSearchConditions,
    PlaceSearchPlan,
)
from daengs_place.place.planning.execution import prefers_parking, purpose_kinds
from daengs_place.place.planning.guard import guard_search_plan
from daengs_place.place.restriction_projection import DogRestrictionEvaluation, project
from daengs_place.place.source_catalog import KCISA_KINDS, KTO_KINDS, MOIS_SOURCES

MEDICAL_KINDS = frozenset({PlaceKind.HOSPITAL, PlaceKind.PHARMACY})
PLACE_KINDS = frozenset(PlaceKind)

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
        f"PlaceKind and resolver mappings differ: missing={missing}, without_resolver={retired}"
    )


class PlaceSearchPreferences(BaseModel):
    """사용자가 명시한 사실 기반 선호. 결과를 제거하지 않는다."""

    model_config = ConfigDict(extra="forbid")

    parking: bool = False


class PlaceDogSnapshot(BaseModel):
    """Caller-resolved values, not an authenticated profile identity.

    ref only correlates this request and response. All unknown is a valid selected dog.
    """

    model_config = ConfigDict(extra="forbid")
    ref: str = Field(min_length=1, max_length=100)
    revision: str | None = Field(None, max_length=100)
    dog_size: DogSize | None = None
    dog_weight_kg: float | None = Field(None, gt=0, le=200)
    dog_age_years: float | None = Field(None, ge=0, le=40)


class PlaceSearchRequest(BaseModel):
    lat: float = Field(ge=32, le=40)
    lng: float = Field(ge=123, le=133)
    radius_m: int = Field(3000, ge=100, le=20000)
    kinds: list[PlaceKind] = Field(min_length=1, max_length=MAX_KINDS_PER_REQUEST)
    # 지도는 반경 안 후보를 가능한 한 보되 서버 상한은 명시한다. 잘렸는지는 group이 말한다.
    limit_per_kind: int | None = Field(None, ge=1, le=MAX_RESULTS)
    conditions: PlaceSearchConditions | None = None
    preferences: PlaceSearchPreferences | None = None
    name_query: PlaceNameQuery = ""
    dogs: list[PlaceDogSnapshot] = Field(default_factory=list, max_length=20)

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
        if self.dogs and self.conditions is not None:
            raise ValueError("dogs and conditions cannot be combined")
        if len({dog.ref for dog in self.dogs}) != len(self.dogs):
            raise ValueError("dog refs must be unique")
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


def compile_place_search_request(request: PlaceSearchRequest) -> PlaceSearchPlan:
    """현행 HTTP 계약을 내부 typed plan으로 옮긴다. 응답 계약은 바꾸지 않는다."""

    return build_place_search_plan(
        lat=request.lat,
        lng=request.lng,
        radius_m=request.radius_m,
        kinds=request.kinds,
        limit_per_kind=request.effective_limit_per_kind,
        conditions=request.conditions,
        prefer_parking=bool(request.preferences and request.preferences.parking),
        name_query=request.name_query,
    )


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
    basis: tuple[Literal["distance_band", "parking", "distance_m"], ...] = ("distance_m",)
    applied: tuple[Literal["parking"], ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    band_m: int | None = Field(None, ge=1, exclude_if=lambda value: value is None)
    coverage: dict[Literal["parking"], BooleanFactCoverage] = Field(
        default_factory=dict,
        exclude_if=lambda value: not value,
    )


class PerDogEvaluation(BaseModel):
    ref: str
    dog_access: DogAccessEvaluation | None = None
    restrictions: DogRestrictionEvaluation | None = None


class PlaceEvaluations(BaseModel):
    dog_access: DogAccessEvaluation | None = Field(
        None,
        exclude_if=lambda value: value is None,
    )
    # 조건 술어를 이 개에 대고 본 결과. `dog_access`(원천 크기 축)와 **다른 축**이라
    # 합치지 않는다 — 원천이 두 곳에 다르게 적었다는 사실 자체가 정보다.
    restrictions: DogRestrictionEvaluation | None = Field(
        None,
        exclude_if=lambda value: value is None,
    )
    dogs: list[PerDogEvaluation] = Field(default_factory=list, exclude_if=lambda value: not value)


class PlaceSearchHit(BaseModel):
    place: PlaceResult
    evaluations: PlaceEvaluations = Field(default_factory=PlaceEvaluations)


class PlaceSearchGroup(BaseModel):
    kind: PlaceKind
    sort: PlaceSort = Field(default_factory=PlaceSort)
    # 의료 그룹에는 없다 — `restrictions` 는 시설 원천(KCISA)만 가진 사실이다.
    restrictions: RestrictionCoverage | None = Field(
        None,
        exclude_if=lambda value: value is None,
    )
    limit: int = Field(ge=1)
    truncated: bool = False
    results: list[PlaceSearchHit]


class PlaceSearchResponse(BaseModel):
    dogs: list[PlaceDogSnapshot] = Field(default_factory=list, exclude_if=lambda value: not value)
    evaluated_at: datetime | None = Field(None, exclude_if=lambda value: value is None)
    # 구버전 서버가 알 수 없는 요청 필드를 무시해도 앱이 이름 검색 성공으로 오인하지 않게.
    name_query: str = Field(default="", exclude_if=lambda value: not value)
    # 평가에 쓴 조건의 에코. 서버가 값을 보정하지 않으므로 요청의 conditions 와 같다 —
    # 그래도 되돌리는 이유는 "무엇을 기준으로 대조했나"를 응답만 보고 알 수 있어야 해서다.
    conditions: PlaceSearchConditions | None = Field(
        None,
        exclude_if=lambda value: value is None,
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
    conditions: PlaceSearchConditions | PlaceDogSnapshot | None,
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
            dog_access=dog_access,
            restrictions=restrictions,
        ),
    )


async def _medical_group(
    db: AsyncSession,
    plan: PlaceSearchPlan,
    kind: PlaceKind,
) -> PlaceSearchGroup:
    spatial = plan.spatial
    limit = plan.limit_per_kind
    rows = await resolve_medical_places(
        db,
        lat=spatial.lat,
        lng=spatial.lng,
        radius_m=spatial.radius_m,
        judge_at=SystemClock().now(),
        kind=kind.value,
        limit=limit + 1,
        name_query=plan.name_query,
    )
    truncated = len(rows) > limit
    places = sorted((medical_place_result(row) for row in rows), key=_distance_key)[:limit]
    results = [_hit(place, plan.conditions) for place in places]
    return PlaceSearchGroup(kind=kind, limit=limit, truncated=truncated, results=results)


async def _facility_group(
    db: AsyncSession,
    plan: PlaceSearchPlan,
    kind: PlaceKind,
) -> PlaceSearchGroup:
    spatial = plan.spatial
    limit = plan.limit_per_kind
    prefer_parking = prefers_parking(plan)
    resolved = await resolve_facilities(
        FacilityParams(
            lat=spatial.lat,
            lng=spatial.lng,
            radius_m=spatial.radius_m,
            kind=kind.value,
            limit=limit,
            only_dog_ok=False,
            parking=prefer_parking,
            name_query=plan.name_query,
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
    results = [_hit(place, plan.conditions) for place in places]
    return PlaceSearchGroup(
        kind=kind,
        sort=sort,
        limit=limit,
        truncated=resolved.truncated,
        results=results,
        restrictions=_restriction_coverage(places),
    )


async def search_place_plan(
    db: AsyncSession,
    plan: PlaceSearchPlan,
) -> PlaceSearchResponse:
    """검증된 plan의 kind 순서를 보존하고 서로 다른 후보군을 전역 순위로 섞지 않는다."""

    plan = guard_search_plan(plan)
    groups: list[PlaceSearchGroup] = []
    for kind in purpose_kinds(plan):
        if kind in MEDICAL_KINDS:
            group = await _medical_group(db, plan, kind)
        else:
            group = await _facility_group(db, plan, kind)
        groups.append(group)
    return PlaceSearchResponse(
        conditions=plan.conditions, groups=groups, name_query=plan.name_query
    )


async def search_place_groups(
    db: AsyncSession,
    request: PlaceSearchRequest,
) -> PlaceSearchResponse:
    """현행 HTTP request를 typed plan으로 컴파일한 뒤 같은 실행기를 호출한다."""

    response = await search_place_plan(db, compile_place_search_request(request))
    return evaluate_search_dogs(response, request.dogs)


def evaluate_search_dogs(
    response: PlaceSearchResponse, dogs: list[PlaceDogSnapshot]
) -> PlaceSearchResponse:
    """Attach per-dog evaluations without querying, filtering or reordering candidates."""
    if dogs:
        # Resolve candidates once per kind; dog count does not multiply DB searches.
        response.dogs = dogs
        response.evaluated_at = SystemClock().now()
        for group in response.groups:
            for hit in group.results:
                hit.evaluations.dogs = [
                    PerDogEvaluation(
                        ref=dog.ref,
                        dog_access=(evaluation := _hit(hit.place, dog).evaluations).dog_access,
                        restrictions=evaluation.restrictions,
                    )
                    for dog in dogs
                ]
    return response
