"""자연어 추측 없이 안정 purpose id를 canonical kind 후보군으로 푼다."""

from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field

from daengs_place.place.planning.compiler import build_place_search_plan
from daengs_place.place.planning.contract import (
    MAX_KINDS_PER_REQUEST,
    GateOrigin,
    PlaceKind,
    PlaceSearchConditions,
    PlaceSearchPlan,
    PlanningModel,
)


class PurposePolicyError(ValueError):
    pass


class PurposeId(StrEnum):
    HEALTHCARE = "healthcare"
    PET_CARE = "pet_care"
    SHOPPING = "shopping"
    DINING = "dining"
    OUTING = "outing"
    CULTURE = "culture"
    LODGING = "lodging"


class PurposeSpec(PlanningModel):
    purpose_id: PurposeId
    description: str = Field(min_length=1)
    kinds: tuple[PlaceKind, ...] = Field(min_length=1)


class PurposeResolution(PlanningModel):
    purpose_ids: tuple[PurposeId, ...] = Field(min_length=1)
    kinds: tuple[PlaceKind, ...] = Field(min_length=1)


PURPOSE_CATALOG: tuple[PurposeSpec, ...] = (
    PurposeSpec(
        purpose_id=PurposeId.HEALTHCARE,
        description="진료 또는 동물용 의약품 이용",
        kinds=(PlaceKind.HOSPITAL, PlaceKind.PHARMACY),
    ),
    PurposeSpec(
        purpose_id=PurposeId.PET_CARE,
        description="미용 또는 위탁 돌봄 서비스 이용",
        kinds=(PlaceKind.GROOMING, PlaceKind.BOARDING),
    ),
    PurposeSpec(
        purpose_id=PurposeId.SHOPPING,
        description="반려동물 용품 또는 일반 쇼핑",
        kinds=(PlaceKind.PET_SHOP, PlaceKind.SHOPPING),
    ),
    PurposeSpec(
        purpose_id=PurposeId.DINING,
        description="카페 또는 식당 방문",
        kinds=(PlaceKind.CAFE, PlaceKind.RESTAURANT),
    ),
    PurposeSpec(
        purpose_id=PurposeId.OUTING,
        description="여행지 또는 레저 활동",
        kinds=(PlaceKind.TRAVEL, PlaceKind.LEISURE),
    ),
    PurposeSpec(
        purpose_id=PurposeId.CULTURE,
        description="박물관·미술관·문예회관 등 문화 활동",
        kinds=(
            PlaceKind.MUSEUM,
            PlaceKind.GALLERY,
            PlaceKind.ARTS_CENTER,
            PlaceKind.CULTURE,
        ),
    ),
    PurposeSpec(
        purpose_id=PurposeId.LODGING,
        description="펜션·호텔 또는 기타 숙박",
        kinds=(PlaceKind.PENSION, PlaceKind.HOTEL, PlaceKind.STAY),
    ),
)

_BY_ID = {spec.purpose_id: spec for spec in PURPOSE_CATALOG}
if len(_BY_ID) != len(PURPOSE_CATALOG):
    raise RuntimeError("purpose ids must be unique")

_CATALOG_KINDS = [kind for spec in PURPOSE_CATALOG for kind in spec.kinds]
if len(set(_CATALOG_KINDS)) != len(_CATALOG_KINDS):
    raise RuntimeError("a canonical kind may belong to only one deterministic purpose")
if set(_CATALOG_KINDS) != set(PlaceKind) - {PlaceKind.ETC}:
    raise RuntimeError("purpose catalog must cover every canonical kind except etc")


def resolve_purposes(purpose_ids: Sequence[PurposeId]) -> PurposeResolution:
    """입력 순서와 무관하게 catalog 순서로 같은 후보군을 만든다."""

    try:
        requested = tuple(PurposeId(value) for value in purpose_ids)
    except ValueError as exc:
        raise PurposePolicyError("unknown purpose id") from exc
    if not requested:
        raise PurposePolicyError("at least one purpose is required")
    if len(set(requested)) != len(requested):
        raise PurposePolicyError("purposes must be unique")

    selected = tuple(spec for spec in PURPOSE_CATALOG if spec.purpose_id in requested)
    kinds = tuple(kind for spec in selected for kind in spec.kinds)
    if len(kinds) > MAX_KINDS_PER_REQUEST:
        raise PurposePolicyError(
            f"resolved purposes exceed the {MAX_KINDS_PER_REQUEST}-kind request boundary"
        )
    return PurposeResolution(
        purpose_ids=tuple(spec.purpose_id for spec in selected),
        kinds=kinds,
    )


def build_purpose_search_plan(
    *,
    lat: float,
    lng: float,
    radius_m: int,
    purpose_ids: Sequence[PurposeId],
    origin: GateOrigin,
    limit_per_kind: int,
    conditions: PlaceSearchConditions | None = None,
    prefer_parking: bool = False,
) -> PlaceSearchPlan:
    """이미 분류된 purpose id를 plan으로 컴파일한다. 자연어 해석은 호출자 책임이다."""

    if origin not in {
        GateOrigin.USER_EXPLICIT,
        GateOrigin.INFERRED,
        GateOrigin.SYSTEM,
    }:
        raise PurposePolicyError(f"purpose policy does not accept origin={origin}")
    resolution = resolve_purposes(purpose_ids)
    locked = origin in {GateOrigin.USER_EXPLICIT, GateOrigin.SYSTEM}
    return build_place_search_plan(
        lat=lat,
        lng=lng,
        radius_m=radius_m,
        kinds=resolution.kinds,
        limit_per_kind=limit_per_kind,
        conditions=conditions,
        prefer_parking=prefer_parking,
        purpose_origin=origin,
        purpose_locked=locked,
        purpose_relaxable=not locked,
        purpose_reason=(
            "deterministic purpose policy selected: "
            + ", ".join(value.value for value in resolution.purpose_ids)
        ),
    )
