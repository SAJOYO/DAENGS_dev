"""검증된 검색 의미를 표시할 canonical fact 우선순위로 옮기는 카탈로그."""

from enum import StrEnum

from pydantic import Field, model_validator

from daengs_place.place.information_needs import InformationNeedId
from daengs_place.place.presentation.contract import PresentationFactId, PresentationModel
from daengs_place.place.source_facts.states import FactState

INFORMATION_NEED_POLICY_ID = "place-information-needs"
INFORMATION_NEED_POLICY_VERSION = "1"


class InformationNeedSafety(StrEnum):
    NORMAL = "normal"
    DECISION = "decision"
    SAFETY = "safety"


class InformationNeedSatisfaction(StrEnum):
    KNOWN = "known"
    KNOWN_NON_EMPTY = "known_non_empty"


class InformationNeedSpec(PresentationModel):
    need_id: InformationNeedId
    promoted_fact_ids: tuple[PresentationFactId, ...] = ()
    # 승격해 참고할 값과 요청을 실제로 충족하는 값은 다를 수 있다. 영업시간 문자열은
    # `지금 영업 중` 판단에 유용하지만 open_now 계산값을 대신하지 않는다.
    satisfying_fact_ids: tuple[PresentationFactId, ...] | None = None
    required_states: tuple[FactState, ...] = (FactState.KNOWN,)
    satisfaction: InformationNeedSatisfaction = InformationNeedSatisfaction.KNOWN
    fallback_notice_code: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9_.:-]+$",
    )
    fallback_notice: str = Field(min_length=1, max_length=500)
    priority: int = Field(ge=0, le=100)
    safety: InformationNeedSafety = InformationNeedSafety.NORMAL

    @model_validator(mode="after")
    def catalog_values_are_unique(self):
        if len(set(self.promoted_fact_ids)) != len(self.promoted_fact_ids):
            raise ValueError("promoted fact ids must be unique")
        if self.satisfying_fact_ids is not None:
            if not self.satisfying_fact_ids or len(set(self.satisfying_fact_ids)) != len(
                self.satisfying_fact_ids
            ):
                raise ValueError("satisfying fact ids must be non-empty and unique")
            if not set(self.satisfying_fact_ids) <= set(self.promoted_fact_ids):
                raise ValueError("satisfying fact ids must also be promoted")
        if not self.required_states or len(set(self.required_states)) != len(self.required_states):
            raise ValueError("required fact states must be non-empty and unique")
        return self


INFORMATION_NEEDS: tuple[InformationNeedSpec, ...] = (
    InformationNeedSpec(
        need_id=InformationNeedId.ACTIVITY_PLAY,
        promoted_fact_ids=(
            PresentationFactId.PET_ACCESS_SCOPE,
            PresentationFactId.PET_ACCESS_ZONES,
            PresentationFactId.PET_AMENITIES_FACILITIES,
        ),
        fallback_notice_code="activity.play.evidence_unavailable",
        fallback_notice="놀이 공간과 동반 가능 구역을 확인할 상세 정보가 없습니다.",
        priority=80,
        safety=InformationNeedSafety.DECISION,
        satisfaction=InformationNeedSatisfaction.KNOWN_NON_EMPTY,
    ),
    InformationNeedSpec(
        need_id=InformationNeedId.PRODUCTS_PURCHASABLE,
        promoted_fact_ids=(PresentationFactId.PET_PRODUCTS_PURCHASABLE,),
        fallback_notice_code="products.purchasable.unverified",
        fallback_notice="판매 상품 상세가 없어 강아지 장난감의 판매나 재고를 확인할 수 없습니다.",
        priority=80,
        safety=InformationNeedSafety.DECISION,
        satisfaction=InformationNeedSatisfaction.KNOWN_NON_EMPTY,
    ),
    InformationNeedSpec(
        need_id=InformationNeedId.OPERATIONS_PARKING,
        promoted_fact_ids=(PresentationFactId.OPERATIONS_PARKING,),
        fallback_notice_code="operations.parking.unknown",
        fallback_notice="주차 가능 여부를 확인할 정보가 없습니다.",
        priority=70,
    ),
    InformationNeedSpec(
        need_id=InformationNeedId.OPERATIONS_OPEN_NOW,
        promoted_fact_ids=(
            PresentationFactId.OPERATIONS_OPEN_NOW,
            PresentationFactId.OPERATIONS_HOURS,
            PresentationFactId.OPERATIONS_CLOSED_DAYS,
        ),
        satisfying_fact_ids=(PresentationFactId.OPERATIONS_OPEN_NOW,),
        fallback_notice_code="operations.open_now.unverified",
        fallback_notice="운영시간 정보만으로 현재 영업 여부를 확정할 수 없습니다.",
        priority=85,
        safety=InformationNeedSafety.DECISION,
    ),
    InformationNeedSpec(
        need_id=InformationNeedId.PET_SIZE,
        promoted_fact_ids=(
            PresentationFactId.PET_SIZE,
            PresentationFactId.PET_RESTRICTIONS,
        ),
        satisfying_fact_ids=(PresentationFactId.PET_SIZE,),
        fallback_notice_code="pet.size.unknown",
        fallback_notice="반려견 크기나 무게 제한을 확인할 정보가 없습니다.",
        priority=95,
        safety=InformationNeedSafety.SAFETY,
        satisfaction=InformationNeedSatisfaction.KNOWN_NON_EMPTY,
    ),
    InformationNeedSpec(
        need_id=InformationNeedId.COST_TRAVEL_DISTANCE,
        promoted_fact_ids=(PresentationFactId.PLACE_DISTANCE,),
        fallback_notice_code="cost.travel_distance.unknown",
        fallback_notice="이동 비용을 대신 비교할 거리 정보가 없습니다.",
        priority=65,
    ),
    InformationNeedSpec(
        need_id=InformationNeedId.COST_PET_FEE,
        promoted_fact_ids=(PresentationFactId.PET_FEE,),
        fallback_notice_code="cost.pet_fee.unavailable",
        fallback_notice="반려동물 추가요금을 비교할 정보가 없습니다.",
        priority=75,
    ),
    InformationNeedSpec(
        need_id=InformationNeedId.COST_ADMISSION,
        fallback_notice_code="cost.admission.unavailable",
        fallback_notice="입장료를 비교할 정보가 없습니다.",
        priority=75,
    ),
    InformationNeedSpec(
        need_id=InformationNeedId.COST_PRODUCT_PRICE,
        fallback_notice_code="cost.product_price.unavailable",
        fallback_notice="상품 가격과 재고를 비교할 정보가 없습니다.",
        priority=75,
    ),
    InformationNeedSpec(
        need_id=InformationNeedId.AMBIENCE_QUIET,
        fallback_notice_code="ambience.quiet.unavailable",
        fallback_notice="조용함을 확인할 근거 데이터가 없어 장소 종류와 거리만 참고해야 합니다.",
        priority=70,
        safety=InformationNeedSafety.DECISION,
    ),
)

_BY_ID = {spec.need_id: spec for spec in INFORMATION_NEEDS}
if len(_BY_ID) != len(INFORMATION_NEEDS):
    raise RuntimeError("information need ids must be unique")
_notice_codes = {spec.fallback_notice_code for spec in INFORMATION_NEEDS}
if len(_notice_codes) != len(INFORMATION_NEEDS):
    raise RuntimeError("information need fallback notice codes must be unique")


def information_need_spec(need_id: InformationNeedId) -> InformationNeedSpec:
    return _BY_ID[need_id]
