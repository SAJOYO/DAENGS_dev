"""정규화된 검색 가설을 사용자에게 보일 안정적인 탐색 lens로 컴파일한다."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from daengs_place.place.information_needs import InformationNeedId
from daengs_place.place.intent.hypotheses import (
    HypothesisMappingScope,
    NormalizedIntentOutput,
    SearchHypothesis,
    SearchHypothesisSet,
    SearchModifier,
    UnresolvedFacet,
)
from daengs_place.place.intent.open_discovery import open_discovery_branch
from daengs_place.place.intent.suggestions import (
    IntentPlanCandidate,
    IntentSuggestionOutcome,
    SuggestionBasis,
)
from daengs_place.place.planning.contract import (
    CapabilityId,
    PlaceKind,
    PlaceSearchConditions,
    PlaceSpatialConstraint,
    PlanningModel,
)
from daengs_place.place.planning.intents import (
    BooleanCapabilityIntent,
    IntentObservation,
    IntentRole,
    KindIntent,
    PlannerRequest,
    PlannerStatus,
    PurposeIntent,
)
from daengs_place.place.planning.planner import compile_intent_plan
from daengs_place.place.planning.purpose import PURPOSE_CATALOG, PurposeId

ConfirmableTarget = Annotated[
    KindIntent | PurposeIntent,
    Field(discriminator="intent_type"),
]


class LensType(StrEnum):
    TARGET = "target"
    MODIFIER = "modifier"
    UNRESOLVED = "unresolved"


class LensMappingScope(StrEnum):
    DIRECT = "direct"
    BROAD = "broad"
    PRODUCT_FALLBACK = "product_fallback"
    OPEN_DISCOVERY = "open_discovery"


class LensAvailability(StrEnum):
    EXECUTABLE = "executable"
    BLOCKED = "blocked"
    DEFERRED = "deferred"
    NEEDS_SELECTION = "needs_selection"
    RESOLVED = "resolved"


class FacetOptionAvailability(StrEnum):
    PROXY = "proxy"
    UNAVAILABLE = "unavailable"


class SearchFacetOption(PlanningModel):
    option_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_.:-]+$")
    display_label: str = Field(min_length=1, max_length=80)
    availability: FacetOptionAvailability
    support_note: str = Field(min_length=1, max_length=300)


class SearchSignalLens(PlanningModel):
    lens_id: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9_.:-]+$")
    display_label: str = Field(min_length=1, max_length=80)
    lens_type: LensType
    availability: LensAvailability
    required: bool
    support_note: str = Field(min_length=1, max_length=300)
    basis_observation_ids: tuple[str, ...] = Field(min_length=1, max_length=60)
    options: tuple[SearchFacetOption, ...] = Field(max_length=10)
    selected_option_id: str | None = Field(
        None,
        max_length=120,
        pattern=r"^[a-z0-9_.:-]+$",
    )

    @model_validator(mode="after")
    def type_matches_options(self) -> Self:
        if self.lens_type is LensType.MODIFIER:
            if (
                self.availability is not LensAvailability.DEFERRED
                or self.options
                or self.selected_option_id is not None
            ):
                raise ValueError("modifier lens must be deferred and cannot carry options")
        elif self.lens_type is LensType.UNRESOLVED:
            if (
                self.availability
                not in {
                    LensAvailability.NEEDS_SELECTION,
                    LensAvailability.RESOLVED,
                }
                or not self.options
            ):
                raise ValueError("facet lens requires selectable options")
            if self.selected_option_id is not None and self.selected_option_id not in {
                item.option_id for item in self.options
            }:
                raise ValueError("selected facet option must belong to the signal lens")
            if (self.availability is LensAvailability.RESOLVED) != (
                self.selected_option_id is not None
            ):
                raise ValueError("resolved facet lens requires exactly one selected option")
        else:
            raise ValueError("signal lens cannot use target type")
        return self


class TargetSearchLens(PlanningModel):
    lens_id: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9_.:-]+$")
    display_label: str = Field(min_length=1, max_length=80)
    target_summary: str = Field(min_length=1, max_length=160)
    lens_type: LensType = LensType.TARGET
    mapping_scope: LensMappingScope
    availability: LensAvailability
    support_note: str = Field(min_length=1, max_length=300)
    candidate: IntentPlanCandidate
    confirmable_targets: tuple[ConfirmableTarget, ...] = Field(min_length=1, max_length=6)
    modifier_ids: tuple[str, ...] = Field(max_length=10)
    information_need_ids: tuple[InformationNeedId, ...] = Field(default=(), max_length=10)
    unresolved_facet_ids: tuple[str, ...] = Field(max_length=10)
    unsupported_signals: tuple[str, ...] = Field(max_length=30)
    confirmation_context: tuple[IntentObservation, ...] = Field(
        default=(),
        max_length=20,
        exclude=True,
    )

    @model_validator(mode="after")
    def availability_matches_candidate(self) -> Self:
        if len(set(self.information_need_ids)) != len(self.information_need_ids):
            raise ValueError("target lens information needs must be unique")
        ready = self.candidate.result.status is PlannerStatus.READY
        if self.availability is LensAvailability.EXECUTABLE:
            if not ready or self.unresolved_facet_ids:
                raise ValueError("executable target lens requires a ready resolved plan")
        elif self.availability is LensAvailability.NEEDS_SELECTION:
            if not ready or not self.unresolved_facet_ids:
                raise ValueError("selection target lens requires a ready plan and unresolved facet")
        elif self.availability is LensAvailability.BLOCKED:
            if ready:
                raise ValueError("blocked target lens cannot carry a ready plan")
        else:
            raise ValueError("target lens cannot be deferred")
        return self


class SearchLensOutcome(PlanningModel):
    target_lenses: tuple[TargetSearchLens, ...] = Field(max_length=15)
    signal_lenses: tuple[SearchSignalLens, ...] = Field(max_length=20)

    @model_validator(mode="after")
    def lens_ids_are_unique(self) -> Self:
        ids = [item.lens_id for item in (*self.target_lenses, *self.signal_lenses)]
        if len(ids) != len(set(ids)):
            raise ValueError("search lens ids must be unique")
        return self

    @property
    def executable_targets(self) -> tuple[TargetSearchLens, ...]:
        return tuple(
            item for item in self.target_lenses if item.availability is LensAvailability.EXECUTABLE
        )


_KIND_LABELS = {
    PlaceKind.HOSPITAL: "동물병원",
    PlaceKind.PHARMACY: "동물약국",
    PlaceKind.PET_SHOP: "펫샵",
    PlaceKind.SHOPPING: "쇼핑",
    PlaceKind.GROOMING: "미용",
    PlaceKind.BOARDING: "돌봄",
    PlaceKind.TRAVEL: "산책·야외",
    PlaceKind.LEISURE: "놀기",
    PlaceKind.MUSEUM: "박물관",
    PlaceKind.GALLERY: "미술관",
    PlaceKind.ARTS_CENTER: "공연·문화",
    PlaceKind.CULTURE: "문화공간",
    PlaceKind.CAFE: "카페",
    PlaceKind.RESTAURANT: "식당",
    PlaceKind.PENSION: "펜션",
    PlaceKind.HOTEL: "호텔",
    PlaceKind.STAY: "숙소",
    PlaceKind.ETC: "기타 장소",
}
_PURPOSE_LABELS = {
    PurposeId.HEALTHCARE: "진료",
    PurposeId.PET_CARE: "돌봄",
    PurposeId.SHOPPING: "용품·쇼핑",
    PurposeId.DINING: "식사·카페",
    PurposeId.OUTING: "나들이",
    PurposeId.CULTURE: "문화공간",
    PurposeId.LODGING: "숙소",
}
_PLAY_LABELS = {
    ":play:dedicated": "놀기",
    ":play:outdoor": "산책·야외",
    ":play:stay-together": "같이 쉬기",
}
_PLAY_NOTES = {
    ":play:dedicated": "레저 분류를 넓게 본 결과이며 반려견 놀이시설 여부는 확인이 필요합니다.",
    ":play:outdoor": "여행·야외 분류를 넓게 본 결과이며 산책 적합성을 보장하지 않습니다.",
    ":play:stay-together": "카페·식당 분류를 넓게 본 결과이며 실내 동반 가능 여부는 별도 확인이 필요합니다.",
}
_FACET_OPTIONS = {
    "cost.travel_distance": SearchFacetOption(
        option_id="cost.travel_distance",
        display_label="가까운 곳",
        availability=FacetOptionAvailability.PROXY,
        support_note="실제 교통비가 아니라 현재 위치와의 거리로 대신 비교할 수 있습니다.",
    ),
    "cost.pet_fee": SearchFacetOption(
        option_id="cost.pet_fee",
        display_label="반려견 추가요금",
        availability=FacetOptionAvailability.UNAVAILABLE,
        support_note="현재 검색 capability에 추가요금 비교가 없습니다.",
    ),
    "cost.admission": SearchFacetOption(
        option_id="cost.admission",
        display_label="입장료",
        availability=FacetOptionAvailability.UNAVAILABLE,
        support_note="입장료 coverage가 없어 비교할 수 없습니다.",
    ),
    "cost.product_price": SearchFacetOption(
        option_id="cost.product_price",
        display_label="물건값",
        availability=FacetOptionAvailability.UNAVAILABLE,
        support_note="상품 가격과 재고 데이터가 없어 비교할 수 없습니다.",
    ),
}
_MODIFIER_INFORMATION_NEEDS = {
    "activity.play": InformationNeedId.ACTIVITY_PLAY,
    "composition.buy_dog_toy": InformationNeedId.PRODUCTS_PURCHASABLE,
    "semantic.quiet": InformationNeedId.AMBIENCE_QUIET,
}
_PRODUCT_INFORMATION_NEED_POLICIES = {
    "activity.buy_dog_toy_implies_pet_shop": InformationNeedId.PRODUCTS_PURCHASABLE,
    "activity.buy_dog_toy_kept_with_target": InformationNeedId.PRODUCTS_PURCHASABLE,
}


def _modifier_information_needs(
    modifiers: tuple[SearchModifier, ...],
) -> tuple[InformationNeedId, ...]:
    return tuple(
        dict.fromkeys(
            _MODIFIER_INFORMATION_NEEDS[item.modifier_id]
            for item in modifiers
            if item.modifier_id in _MODIFIER_INFORMATION_NEEDS
        )
    )


def _hypothesis_information_needs(
    hypothesis: SearchHypothesis,
    modifiers: tuple[SearchModifier, ...],
) -> tuple[InformationNeedId, ...]:
    needs = list(_modifier_information_needs(modifiers))
    for receipt in hypothesis.relation_receipts:
        if receipt.policy_id.startswith("activity.play_expands_to_"):
            needs.append(InformationNeedId.ACTIVITY_PLAY)
        elif receipt.policy_id in _PRODUCT_INFORMATION_NEED_POLICIES:
            needs.append(_PRODUCT_INFORMATION_NEED_POLICIES[receipt.policy_id])
    return tuple(dict.fromkeys(needs))


def _target_label(targets: tuple[ConfirmableTarget, ...]) -> str:
    labels = []
    for target in targets:
        if isinstance(target, KindIntent):
            labels.append(_KIND_LABELS[target.kind])
        else:
            labels.append(_PURPOSE_LABELS[target.purpose_id])
    return "·".join(dict.fromkeys(labels))


def _hypothesis_label(hypothesis: SearchHypothesis) -> str:
    if hypothesis.policy_branch_id is not None:
        return open_discovery_branch(hypothesis.policy_branch_id).display_label
    for suffix, label in _PLAY_LABELS.items():
        if hypothesis.hypothesis_key.endswith(suffix):
            return label
    return _target_label(tuple(target.intent for target in hypothesis.targets))


def _hypothesis_note(hypothesis: SearchHypothesis) -> str:
    if hypothesis.policy_branch_id is not None:
        return open_discovery_branch(hypothesis.policy_branch_id).support_note
    for suffix, note in _PLAY_NOTES.items():
        if hypothesis.hypothesis_key.endswith(suffix):
            return note
    if (
        any(
            isinstance(target.intent, KindIntent) and target.intent.kind is PlaceKind.PET_SHOP
            for target in hypothesis.targets
        )
        and hypothesis.mapping_scope is HypothesisMappingScope.COMPOSED
    ):
        return "반려동물용품점 분류와 직접 연결했지만 강아지 장난감의 현재 판매·재고는 보장하지 않습니다."
    if any(
        item.policy_id == "activity.buy_dog_toy_kept_with_target"
        for item in hypothesis.relation_receipts
    ):
        return "요청한 장소 분류만 검색하며 강아지 장난감의 현재 판매·재고는 보장하지 않습니다."
    return "사용자 발화와 현재 canonical 장소 분류를 직접 연결한 탐색 방향입니다."


def _unsupported_signals(
    candidate: IntentPlanCandidate,
    modifiers: tuple[SearchModifier, ...],
) -> tuple[str, ...]:
    result = candidate.result
    issue_codes = (
        item.code for item in (*result.not_applied, *result.unsupported, *result.clarifications)
    )
    modifier_codes = (f"{item.modifier_id}:{item.execution.value}" for item in modifiers)
    return tuple(dict.fromkeys((*issue_codes, *modifier_codes)))


def _availability(
    candidate: IntentPlanCandidate,
    facets: tuple[UnresolvedFacet, ...],
) -> LensAvailability:
    if candidate.result.status is not PlannerStatus.READY:
        return LensAvailability.BLOCKED
    if any(item.blocking for item in facets):
        return LensAvailability.NEEDS_SELECTION
    return LensAvailability.EXECUTABLE


def _compile_hypothesis(
    hypothesis_set: SearchHypothesisSet,
    hypothesis: SearchHypothesis,
    *,
    spatial: PlaceSpatialConstraint,
    limit_per_kind: int,
    conditions: PlaceSearchConditions | None,
) -> IntentPlanCandidate:
    return IntentPlanCandidate(
        candidate_key=hypothesis.hypothesis_key,
        basis=(
            SuggestionBasis.OPEN_DISCOVERY
            if hypothesis.mapping_scope is HypothesisMappingScope.PRODUCT_POLICY
            else SuggestionBasis.HYPOTHESIS
        ),
        basis_observation_ids=hypothesis.basis_observation_ids,
        basis_policy_id=hypothesis.policy_id,
        basis_policy_version=hypothesis.policy_version,
        basis_policy_branch_id=hypothesis.policy_branch_id,
        result=compile_intent_plan(
            PlannerRequest(
                spatial=spatial,
                observations=hypothesis_set.planner_observations(hypothesis),
                limit_per_kind=limit_per_kind,
                conditions=conditions,
            )
        ),
    )


def _hypothesis_lens(
    hypothesis_set: SearchHypothesisSet,
    hypothesis: SearchHypothesis,
    *,
    spatial: PlaceSpatialConstraint,
    limit_per_kind: int,
    conditions: PlaceSearchConditions | None,
) -> TargetSearchLens:
    candidate = _compile_hypothesis(
        hypothesis_set,
        hypothesis,
        spatial=spatial,
        limit_per_kind=limit_per_kind,
        conditions=conditions,
    )
    targets = tuple(target.intent for target in hypothesis.targets)
    label = _hypothesis_label(hypothesis)
    return TargetSearchLens(
        lens_id=f"lens:{hypothesis.hypothesis_key}",
        display_label=f"#{label}",
        target_summary=label,
        mapping_scope=(
            LensMappingScope.OPEN_DISCOVERY
            if hypothesis.mapping_scope is HypothesisMappingScope.PRODUCT_POLICY
            else (
                LensMappingScope.BROAD
                if hypothesis.mapping_scope is HypothesisMappingScope.EXPANDED
                else LensMappingScope.DIRECT
            )
        ),
        availability=_availability(candidate, hypothesis_set.unresolved_facets),
        support_note=_hypothesis_note(hypothesis),
        candidate=candidate,
        confirmable_targets=targets,
        modifier_ids=tuple(item.modifier_id for item in hypothesis_set.modifiers),
        information_need_ids=_hypothesis_information_needs(
            hypothesis,
            hypothesis_set.modifiers,
        ),
        unresolved_facet_ids=tuple(
            item.facet_id for item in hypothesis_set.unresolved_facets if item.blocking
        ),
        unsupported_signals=_unsupported_signals(candidate, hypothesis_set.modifiers),
        confirmation_context=hypothesis_set.common,
    )


def _fallback_targets(candidate: IntentPlanCandidate) -> tuple[ConfirmableTarget, ...]:
    key = candidate.candidate_key
    if ":fallback:kind:" in key:
        return (KindIntent(kind=PlaceKind(key.rsplit(":", 1)[1])),)
    if ":fallback:purpose:" in key:
        return (PurposeIntent(purpose_id=PurposeId(key.rsplit(":", 1)[1])),)
    plan = candidate.result.plan
    if plan is None:
        raise ValueError("ready fallback candidate requires a plan")
    gate = next(item for item in plan.gates if item.capability_id is CapabilityId.PURPOSE_KIND)
    if not isinstance(gate.value, tuple):
        raise TypeError("purpose gate must carry kinds")
    for spec in PURPOSE_CATALOG:
        if gate.value == spec.kinds:
            return (PurposeIntent(purpose_id=spec.purpose_id),)
    return tuple(KindIntent(kind=kind) for kind in gate.value)


def _fallback_lens(
    candidate: IntentPlanCandidate,
    *,
    modifiers: tuple[SearchModifier, ...],
    facets: tuple[UnresolvedFacet, ...],
    confirmation_context: tuple[IntentObservation, ...],
) -> TargetSearchLens:
    targets = _fallback_targets(candidate)
    label = _target_label(targets)
    quiet = any(item.modifier_id == "semantic.quiet" for item in modifiers)
    note = (
        "조용함을 확인한 결과가 아니라 가능한 장소 방향을 먼저 펼친 것입니다. 장소 설명·유형 기반 조용함 랭킹은 아직 적용되지 않았습니다."
        if quiet
        else "직접 실행할 target이 없어 제품이 관리하는 넓은 탐색 방향을 제안했습니다."
    )
    return TargetSearchLens(
        lens_id=f"lens:{candidate.candidate_key}",
        display_label=f"#{label}",
        target_summary=label,
        mapping_scope=LensMappingScope.PRODUCT_FALLBACK,
        availability=_availability(candidate, facets),
        support_note=note,
        candidate=candidate,
        confirmable_targets=targets,
        modifier_ids=tuple(item.modifier_id for item in modifiers),
        information_need_ids=_modifier_information_needs(modifiers),
        unresolved_facet_ids=tuple(item.facet_id for item in facets if item.blocking),
        unsupported_signals=_unsupported_signals(candidate, modifiers),
        confirmation_context=confirmation_context,
    )


def _fallback_hypothesis_set(
    candidate: IntentPlanCandidate,
    sets_by_key: dict[str, SearchHypothesisSet],
) -> SearchHypothesisSet:
    set_key, separator, _ = candidate.candidate_key.partition(":fallback:")
    if not separator or set_key not in sets_by_key:
        raise ValueError("fallback candidate must belong to a normalized hypothesis set")
    return sets_by_key[set_key]


def _fallback_confirmation_context(
    hypothesis_set: SearchHypothesisSet,
) -> tuple[IntentObservation, ...]:
    """Product fallback planner가 실제로 보존한 preference만 확인 뒤 재조립한다."""

    return tuple(
        observation
        for observation in hypothesis_set.common
        if isinstance(observation.intent, BooleanCapabilityIntent)
        and observation.role is IntentRole.PREFERENCE
    )


def _modifier_signal(modifier: SearchModifier, set_key: str) -> SearchSignalLens:
    if modifier.modifier_id == "semantic.quiet":
        label = "#조용한 분위기"
        note = (
            "사용자가 필수로 요청한 조용함을 보존했지만 판정할 장소 evidence와 ranker가 아직 없습니다."
            if modifier.required
            else "사용자 선호는 보존했지만 조용함을 판정할 장소 evidence와 ranker가 아직 없습니다."
        )
    elif modifier.modifier_id == "semantic.dog_interest":
        label = "#강아지 관심 가능성"
        note = "강아지가 좋아할 만한 요소를 요청한 것은 보존했지만 이를 판정할 장소 evidence와 ranker가 아직 없습니다."
    elif modifier.modifier_id == "activity.play":
        label = "#함께 놀기"
        note = "사용자가 하려는 활동으로 보존했지만 놀이 적합성을 판정할 장소 evidence가 아직 없습니다."
    elif modifier.modifier_id == "composition.buy_dog_toy":
        label = "#강아지 장난감 구매"
        note = "구매 목적을 보존했지만 장소별 상품 판매·재고 데이터가 아직 없습니다."
    else:
        label = f"#{modifier.modifier_id}"
        note = "사용자 선호를 보존했지만 현재 검색에 적용할 수 없습니다."
    return SearchSignalLens(
        lens_id=f"signal:{set_key}:modifier:{modifier.modifier_id}",
        display_label=label,
        lens_type=LensType.MODIFIER,
        availability=LensAvailability.DEFERRED,
        required=modifier.required,
        support_note=note,
        basis_observation_ids=modifier.basis_observation_ids,
        options=(),
    )


def _facet_signal(facet: UnresolvedFacet, set_key: str) -> SearchSignalLens:
    label = "#비용 기준 선택" if facet.facet_id == "cost.dimension" else f"#{facet.facet_id}"
    return SearchSignalLens(
        lens_id=f"signal:{set_key}:facet:{facet.facet_id}",
        display_label=label,
        lens_type=LensType.UNRESOLVED,
        availability=LensAvailability.NEEDS_SELECTION,
        required=facet.blocking,
        support_note="어떤 비용을 뜻하는지 선택하기 전에는 가격 조건을 적용하지 않습니다.",
        basis_observation_ids=facet.basis_observation_ids,
        options=tuple(_FACET_OPTIONS[item] for item in facet.options),
    )


def compile_search_lenses(
    normalized: NormalizedIntentOutput,
    suggestion_outcome: IntentSuggestionOutcome,
    *,
    spatial: PlaceSpatialConstraint,
    limit_per_kind: int,
    conditions: PlaceSearchConditions | None = None,
) -> SearchLensOutcome:
    """독립 가설을 plan으로 컴파일하고, fallback·미지원 신호를 표시 계약에 합친다."""

    targets = tuple(
        _hypothesis_lens(
            hypothesis_set,
            hypothesis,
            spatial=spatial,
            limit_per_kind=limit_per_kind,
            conditions=conditions,
        )
        for hypothesis_set in normalized.hypothesis_sets
        for hypothesis in hypothesis_set.hypotheses
    )
    if not targets:
        sets_by_key = {
            hypothesis_set.hypothesis_set_key: hypothesis_set
            for hypothesis_set in normalized.hypothesis_sets
        }
        fallback_lenses = []
        for candidate in suggestion_outcome.suggestions:
            hypothesis_set = _fallback_hypothesis_set(candidate, sets_by_key)
            fallback_lenses.append(
                _fallback_lens(
                    candidate,
                    modifiers=hypothesis_set.modifiers,
                    facets=hypothesis_set.unresolved_facets,
                    confirmation_context=_fallback_confirmation_context(hypothesis_set),
                )
            )
        targets = tuple(fallback_lenses)
    signals = tuple(
        signal
        for hypothesis_set in normalized.hypothesis_sets
        for signal in (
            *(
                _modifier_signal(item, hypothesis_set.hypothesis_set_key)
                for item in hypothesis_set.modifiers
            ),
            *(
                _facet_signal(item, hypothesis_set.hypothesis_set_key)
                for item in hypothesis_set.unresolved_facets
            ),
        )
    )
    return SearchLensOutcome(target_lenses=targets, signal_lenses=signals)
