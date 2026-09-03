"""source raw → canonical facts의 **내부** 계약.

`daengs_place.place.contracts`는 웹·Android가 받는 외부 계약이다. 이 모델은 그 파일을 확장하지 않고
source별 의미를 shadow로 비교하기 위한 값 객체다. adapter/read path가 전환되기 전까지 외부
응답에는 노출되지 않는다.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from daengs_place.place.source_facts.states import (
    EvidenceCertainty,
    FactState,
    ProjectionState,
)


class InternalModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FactEvidence(InternalModel):
    """경로 하나의 값이 어디서, 어떤 상태로 나왔는가."""

    state: FactState
    source_field: str = Field(min_length=1)
    raw_value: Any = None
    parser_version: str = Field(min_length=1)
    certainty: EvidenceCertainty = EvidenceCertainty.SOURCE
    note: str | None = None


class TaxonomyNode(InternalModel):
    code: str = Field(min_length=1)
    label: str | None = None


class PurposeFacts(InternalModel):
    """장소가 무엇을 위한 곳인가. 위치·입장조건과 섞지 않는다."""

    primary: str | None = None
    subtype_code: str | None = None
    taxonomy_path: tuple[TaxonomyNode, ...] = ()


class PetAccessFacts(InternalModel):
    """동반 가능성과 구역 힌트. 견별 조건은 `restrictions.predicates`에 둔다."""

    allowed: bool | None = None
    scope: Literal["full", "partial"] | None = None
    exclusive: bool | None = None
    # KCISA 컬럼의 원문 의미를 지우지 않는다. zone_hints는 그 두 값을 보수적으로 투영한 값이고,
    # 명시적인 제한 문장과 충돌하면 issue가 생긴다.
    source_indoor: bool | None = None
    source_outdoor: bool | None = None
    zone_hints: tuple[Literal["indoor", "outdoor"], ...] = ()
    # KTO acmpyPsblCpam처럼 허용 대상을 자유문장으로 주는 원천은 원문도 보존한다.
    companion_text: str | None = None


class RestrictionPredicate(InternalModel):
    """기존 restriction vocabulary와 같은 구조의 원천 독립 술어."""

    code: str = Field(min_length=1)
    applies_to: str = "all"
    params: dict[str, str] = Field(default_factory=dict)
    certainty: Literal["firm", "soft"] = "firm"


class RestrictionFacts(InternalModel):
    state: Literal["unknown", "none_confirmed", "not_applicable", "restricted"] = "unknown"
    parse_state: Literal["mapped", "partial", "raw_only"] | None = None
    predicates: tuple[RestrictionPredicate, ...] = ()
    # 원천마다 제한이 여러 필드에 분산된다. 필드명을 버리지 않는다.
    raw: dict[str, str] = Field(default_factory=dict)


class PetAmenityFacts(InternalModel):
    facilities: tuple[str, ...] = ()
    provided_products: tuple[str, ...] = ()
    purchasable_products: tuple[str, ...] = ()
    rentable_products: tuple[str, ...] = ()


class PetFeeFacts(InternalModel):
    raw_text: str | None = None
    amount_krw: int | None = Field(default=None, ge=0)


class OperationFacts(InternalModel):
    hours_text: str | None = None
    closed_days: str | None = None
    parking: bool | None = None


class ProjectionIssue(InternalModel):
    """조용히 추측하거나 버리지 말아야 할 source-level 충돌."""

    code: str = Field(min_length=1)
    paths: tuple[str, ...] = ()
    detail: str


class SourceFactProjection(InternalModel):
    """원천 레코드 한 건의 canonical fact 후보. 통합된 물리 Place가 아니다."""

    source: Literal["kcisa", "kto"]
    parser_version: str = Field(min_length=1)
    state: ProjectionState = ProjectionState.COMPLETE
    purpose: PurposeFacts = Field(default_factory=PurposeFacts)
    pet_access: PetAccessFacts = Field(default_factory=PetAccessFacts)
    restrictions: RestrictionFacts = Field(default_factory=RestrictionFacts)
    amenities: PetAmenityFacts = Field(default_factory=PetAmenityFacts)
    pet_fee: PetFeeFacts = Field(default_factory=PetFeeFacts)
    operations: OperationFacts = Field(default_factory=OperationFacts)
    # 키는 내부 canonical path다. 값과 상태를 같은 enum으로 합치지 않는다.
    evidence: dict[str, FactEvidence] = Field(default_factory=dict)
    issues: tuple[ProjectionIssue, ...] = ()
