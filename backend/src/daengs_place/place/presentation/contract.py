"""Place 사실과 UI 레이아웃 사이의 semantic presentation 계약.

이 계약은 검색 후보나 순위를 바꾸지 않는다. source fact의 상태, 사용자 조건과의 평가,
값을 빌린 경로를 서로 다른 축으로 보존한 채 어느 의미 영역에 둘지만 표현한다.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from daengs_place.place.contracts import PlaceRef
from daengs_place.place.source_facts.states import EvidenceCertainty, FactState


class PresentationModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PresentationFactId(StrEnum):
    PLACE_KIND = "place.kind"
    PLACE_DISTANCE = "place.distance"
    PLACE_ADDRESS = "place.address"
    PET_ACCESS_ALLOWED = "pet_access.allowed"
    PET_ACCESS_SCOPE = "pet_access.scope"
    PET_ACCESS_ZONES = "pet_access.zones"
    PET_ACCESS_COMPANION = "pet_access.companion"
    PET_SIZE = "pet_access.size"
    PET_RESTRICTIONS = "restrictions.predicates"
    PET_AMENITIES_FACILITIES = "amenities.facilities"
    PET_PRODUCTS_PROVIDED = "amenities.products.provided"
    PET_PRODUCTS_PURCHASABLE = "amenities.products.purchasable"
    PET_PRODUCTS_RENTABLE = "amenities.products.rentable"
    PET_FEE = "pet_fee.amount"
    OPERATIONS_PARKING = "operations.parking"
    OPERATIONS_OPEN_NOW = "operations.open_now"
    OPERATIONS_HOURS = "operations.hours"
    OPERATIONS_CLOSED_DAYS = "operations.closed_days"
    CONTACT_PHONE = "contact.phone"
    CONTACT_HOMEPAGE = "contact.homepage"


class PresentationPlacement(StrEnum):
    CORE = "core"
    PROMOTED = "promoted"
    DETAIL = "detail"


class PresentationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class EvaluationState(StrEnum):
    COMPATIBLE = "compatible"
    CONDITIONAL = "conditional"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"
    NOT_EVALUATED = "not_evaluated"


class SourceRole(StrEnum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"


class ValueOrigin(StrEnum):
    OWN = "own"
    BORROWED = "borrowed"


class LinkState(StrEnum):
    CANDIDATE = "candidate"
    VERIFIED = "verified"


class DecisionRole(StrEnum):
    DISPLAY = "display"
    RANKING = "ranking"
    EVALUATION = "evaluation"
    FILTERING = "filtering"


class PresentationLinkReceipt(PresentationModel):
    """대표 레코드와 보조 레코드를 연결한 근거. identity 합의가 아니다."""

    primary: PlaceRef
    supporting: PlaceRef
    method: str = Field(min_length=1, max_length=80)
    distance_m: int = Field(ge=0)
    state: LinkState

    @model_validator(mode="after")
    def link_distinct_records(self) -> Self:
        if self.primary == self.supporting:
            raise ValueError("a presentation link must connect distinct source records")
        return self


class PresentationProvenance(PresentationModel):
    """값 출처와 그 값이 결과에 들어온 경로. 파생 여부는 별도 축이다."""

    source: PlaceRef
    source_role: SourceRole
    value_origin: ValueOrigin
    evidence_certainty: EvidenceCertainty = EvidenceCertainty.SOURCE
    source_field: str | None = Field(None, min_length=1, max_length=160)
    as_of: str | None = None
    link: PresentationLinkReceipt | None = None

    @model_validator(mode="after")
    def borrowed_values_require_a_supporting_link(self) -> Self:
        if self.value_origin is ValueOrigin.BORROWED:
            if self.source_role is not SourceRole.SUPPORTING or self.link is None:
                raise ValueError("borrowed values require a supporting source and link receipt")
            if self.source != self.link.supporting:
                raise ValueError("borrowed value source must match the supporting link source")
        elif self.source_role is not SourceRole.PRIMARY or self.link is not None:
            raise ValueError("own values must come from the primary source without a link")
        return self


class PresentationFact(PresentationModel):
    """배치 전의 effective fact. 어느 원천 값을 채택할지는 앞 계층의 책임이다."""

    fact_id: PresentationFactId
    label: str = Field(min_length=1, max_length=80)
    value: JsonValue = None
    display_text: str = Field(min_length=1, max_length=500)
    source_state: FactState
    evaluation_state: EvaluationState = EvaluationState.NOT_EVALUATED
    severity: PresentationSeverity = PresentationSeverity.INFO
    provenance: PresentationProvenance
    decision_roles: tuple[DecisionRole, ...] = (DecisionRole.DISPLAY,)

    @model_validator(mode="after")
    def state_and_roles_are_consistent(self) -> Self:
        if self.source_state is FactState.KNOWN and self.value is None:
            raise ValueError("known presentation fact requires a value")
        if self.source_state is not FactState.KNOWN and self.value is not None:
            raise ValueError("non-known presentation fact cannot carry a canonical value")
        if not self.decision_roles:
            raise ValueError("presentation fact requires at least one decision role")
        if len(set(self.decision_roles)) != len(self.decision_roles):
            raise ValueError("presentation fact decision roles must be unique")
        if (
            self.evaluation_state
            in {
                EvaluationState.COMPATIBLE,
                EvaluationState.CONDITIONAL,
                EvaluationState.INCOMPATIBLE,
            }
            and self.source_state is not FactState.KNOWN
        ):
            raise ValueError("a conclusive evaluation requires a known source fact")
        return self


class PresentationItem(PresentationFact):
    placement: PresentationPlacement
    promoted_by: tuple[str, ...] = ()

    @model_validator(mode="after")
    def promotion_has_a_policy_reason(self) -> Self:
        if self.placement is PresentationPlacement.PROMOTED and not self.promoted_by:
            raise ValueError("promoted presentation item requires an information need")
        if self.placement is not PresentationPlacement.PROMOTED and self.promoted_by:
            raise ValueError("only promoted presentation items may carry promotion reasons")
        if len(set(self.promoted_by)) != len(self.promoted_by):
            raise ValueError("presentation promotion reasons must be unique")
        return self


class PresentationNotice(PresentationModel):
    code: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_.:-]+$")
    message: str = Field(min_length=1, max_length=500)
    severity: PresentationSeverity
    fact_ids: tuple[PresentationFactId, ...] = ()

    @model_validator(mode="after")
    def fact_ids_are_unique(self) -> Self:
        if len(set(self.fact_ids)) != len(self.fact_ids):
            raise ValueError("presentation notice fact ids must be unique")
        return self


class WhyMatchedReason(PresentationModel):
    code: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_.:-]+$")
    message: str = Field(min_length=1, max_length=500)
    receipt_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def receipt_ids_are_unique(self) -> Self:
        if len(set(self.receipt_ids)) != len(self.receipt_ids):
            raise ValueError("why-matched receipt ids must be unique")
        return self


class SourceEvidenceSection(PresentationModel):
    """상단에 채택된 값의 원천 근거만 담는다. 연결 후보의 전체 사실을 암묵 병합하지 않는다."""

    source: PlaceRef
    source_role: SourceRole
    adopted_fact_ids: tuple[PresentationFactId, ...]
    link: PresentationLinkReceipt | None = None

    @model_validator(mode="after")
    def supporting_sections_require_a_link(self) -> Self:
        if not self.adopted_fact_ids:
            raise ValueError("source evidence section requires at least one adopted fact")
        if len(set(self.adopted_fact_ids)) != len(self.adopted_fact_ids):
            raise ValueError("source evidence fact ids must be unique")
        if self.source_role is SourceRole.SUPPORTING:
            if self.link is None or self.source != self.link.supporting:
                raise ValueError("supporting evidence requires its link receipt")
        elif self.link is not None:
            raise ValueError("primary evidence cannot carry a supporting link")
        return self


class PresentationPolicyReceipt(PresentationModel):
    policy_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_.:-]+$")
    policy_version: str = Field(min_length=1, max_length=40)
    information_need_policy_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9_.:-]+$",
    )
    information_need_policy_version: str = Field(min_length=1, max_length=40)
    information_need_ids: tuple[str, ...] = ()
    applied_rule_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def receipt_entries_are_unique(self) -> Self:
        if len(set(self.information_need_ids)) != len(self.information_need_ids):
            raise ValueError("information need receipt ids must be unique")
        if len(set(self.applied_rule_ids)) != len(self.applied_rule_ids):
            raise ValueError("presentation applied rule ids must be unique")
        return self


def _validate_item_groups(
    core_items: tuple[PresentationItem, ...],
    promoted_items: tuple[PresentationItem, ...],
    detail_items: tuple[PresentationItem, ...],
) -> None:
    expected = (
        (core_items, PresentationPlacement.CORE),
        (promoted_items, PresentationPlacement.PROMOTED),
        (detail_items, PresentationPlacement.DETAIL),
    )
    items = []
    for group, placement in expected:
        if any(item.placement is not placement for item in group):
            raise ValueError(f"{placement.value} item group has a different placement")
        items.extend(group)
    fact_ids = [item.fact_id for item in items]
    if len(set(fact_ids)) != len(fact_ids):
        raise ValueError("a summary fact may appear in only one presentation placement")


class PresentationPolicyResult(PresentationModel):
    core_items: tuple[PresentationItem, ...] = ()
    promoted_items: tuple[PresentationItem, ...] = ()
    detail_items: tuple[PresentationItem, ...] = ()
    notices: tuple[PresentationNotice, ...] = ()
    policy_receipt: PresentationPolicyReceipt

    @model_validator(mode="after")
    def placements_do_not_overlap(self) -> Self:
        _validate_item_groups(self.core_items, self.promoted_items, self.detail_items)
        notice_codes = [notice.code for notice in self.notices]
        if len(set(notice_codes)) != len(notice_codes):
            raise ValueError("presentation notice codes must be unique")
        return self


class PlacePresentation(PresentationModel):
    """카드, 지도 팝업, 상세 sheet가 공유하는 semantic view model."""

    place_key: PlaceRef
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=500)
    kind_id: str = Field(min_length=1, max_length=80)
    kind_label: str = Field(min_length=1, max_length=80)
    distance_m: int = Field(ge=0)
    address: str | None = None
    core_items: tuple[PresentationItem, ...] = ()
    promoted_items: tuple[PresentationItem, ...] = ()
    detail_items: tuple[PresentationItem, ...] = ()
    notices: tuple[PresentationNotice, ...] = ()
    why_matched: tuple[WhyMatchedReason, ...] = ()
    source_evidence: tuple[SourceEvidenceSection, ...] = ()
    policy_receipt: PresentationPolicyReceipt

    @model_validator(mode="after")
    def placements_do_not_overlap(self) -> Self:
        _validate_item_groups(self.core_items, self.promoted_items, self.detail_items)
        items = {
            item.fact_id: item
            for item in (*self.core_items, *self.promoted_items, *self.detail_items)
        }
        evidence_ids = [
            fact_id for section in self.source_evidence for fact_id in section.adopted_fact_ids
        ]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("an adopted fact may belong to only one source evidence section")
        if set(evidence_ids) != set(items):
            raise ValueError("source evidence must cover every presented fact exactly once")
        for section in self.source_evidence:
            if section.source_role is SourceRole.PRIMARY and section.source != self.place_key:
                raise ValueError("primary source evidence must match the presentation place key")
            if (
                section.source_role is SourceRole.SUPPORTING
                and section.link is not None
                and section.link.primary != self.place_key
            ):
                raise ValueError("supporting source link must start at the presentation place key")
            for fact_id in section.adopted_fact_ids:
                provenance = items[fact_id].provenance
                if (
                    provenance.source != section.source
                    or provenance.source_role is not section.source_role
                    or provenance.link != section.link
                ):
                    raise ValueError("source evidence must match the presented fact provenance")
        return self
