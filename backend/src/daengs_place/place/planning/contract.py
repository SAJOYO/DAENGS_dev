"""여러 Place resolver가 실행할 검색 계획의 내부 계약."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from daengs_place.place.contracts import DogSize

MAX_KINDS_PER_REQUEST = 6
MAX_RESULTS_PER_KIND = 3000
MAX_TOTAL_RESULTS = 5000


class PlanningModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


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


class CapabilityId(StrEnum):
    PURPOSE_KIND = "purpose.kind"
    OPERATIONS_PARKING = "operations.parking"


class GateMode(StrEnum):
    OFF = "off"
    PREFER = "prefer"
    FILTER = "filter"


class UnknownPolicy(StrEnum):
    KEEP = "keep"
    SEPARATE = "separate"
    EXCLUDE = "exclude"


class GateOrigin(StrEnum):
    USER_EXPLICIT = "user_explicit"
    USER_PREFERENCE = "user_preference"
    PROFILE = "profile"
    CONTEXT = "context"
    INFERRED = "inferred"
    SYSTEM = "system"


class GateOperator(StrEnum):
    EQ = "eq"
    IN = "in"


GateValue = bool | tuple[PlaceKind, ...]


class SearchGate(PlanningModel):
    """안정 capability id를 통해서만 검색 강도를 표현한다."""

    capability_id: CapabilityId
    mode: GateMode
    operator: GateOperator
    value: GateValue
    unknown_policy: UnknownPolicy
    origin: GateOrigin
    locked: bool = False
    relaxable: bool = True

    @model_validator(mode="after")
    def lock_cannot_be_relaxable(self) -> Self:
        if self.locked and self.relaxable:
            raise ValueError("locked gate cannot be relaxable")
        return self


class PlaceSpatialConstraint(PlanningModel):
    """목적·사실 gate와 독립적으로 실행되는 공간 후보 경계."""

    lat: float = Field(ge=32, le=40)
    lng: float = Field(ge=123, le=133)
    radius_m: int = Field(ge=100, le=20000)


class PlaceSearchConditions(PlanningModel):
    """검색 결과에 대조할 반려견 사실. 현재는 후보를 제거하지 않는다.

    identity가 아니라 값만 받는다. dog_id를 크기·무게·나이로 투영하는 일은 프로필 소유자의
    책임이고, 이 plan은 받은 값을 보정하거나 추측하지 않는다.

    모르는 키는 거부한다. 판정 의미가 있는 입력의 오타나 옛 계약을 조용히 무시하면 덜
    개인화된 결과가 정상 응답처럼 보이므로 계약 오류를 즉시 드러내는 편이 낫다.
    """

    dog_size: DogSize | None = None
    dog_weight_kg: float | None = Field(None, gt=0, le=200)
    # deny:age 술어를 대조하는 유일한 재료다. 나이도 프로필 사실이므로 서버가 채우지 않는다.
    dog_age_years: float | None = Field(None, ge=0, le=40)

    @model_validator(mode="after")
    def require_a_dog_subject(self) -> Self:
        if self.dog_size is None and self.dog_weight_kg is None and self.dog_age_years is None:
            raise ValueError(
                "conditions require at least one of dog_size, dog_weight_kg, dog_age_years"
            )
        return self


class PlanTraceEntry(PlanningModel):
    action: Literal["compiled", "set", "relaxed"]
    capability_id: CapabilityId
    origin: GateOrigin
    reason: str = Field(min_length=1)


class PlanTrace(PlanningModel):
    entries: tuple[PlanTraceEntry, ...] = ()


class PlaceSearchPlan(PlanningModel):
    """같은 값이면 AI 유무와 관계없이 같은 검색을 실행하는 내부 계획."""

    spatial: PlaceSpatialConstraint
    gates: tuple[SearchGate, ...] = Field(min_length=1)
    limit_per_kind: int = Field(ge=1, le=MAX_RESULTS_PER_KIND)
    conditions: PlaceSearchConditions | None = None
    trace: PlanTrace = Field(default_factory=PlanTrace)
