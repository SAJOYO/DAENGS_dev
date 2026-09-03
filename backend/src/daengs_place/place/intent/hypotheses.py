"""Grounded intent를 제품 소유의 독립 검색 가설로 정규화한다.

이 계층은 장소 사실을 추측하거나 plan을 실행하지 않는다. LLM이 관찰한 활동·객체·semantic
신호 사이에서 catalog에 선언된 관계만 적용하고, 공통 제약과 아직 풀리지 않은 facet을 보존한다.
"""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from daengs_place.place.intent.contract import (
    GroundedSearchDirective,
    MaterializedIntentOutput,
    ProposalDisposition,
    SearchModeId,
)
from daengs_place.place.intent.open_discovery import OPEN_DISCOVERY_POLICY
from daengs_place.place.planning.contract import PlaceKind, PlanningModel
from daengs_place.place.planning.intents import (
    ActivityId,
    ActivityIntent,
    IntentObservation,
    IntentProposal,
    IntentRole,
    IntentSource,
    KindIntent,
    ObjectIntent,
    PurposeIntent,
    SearchObjectId,
    SemanticIntent,
    observe_intent,
)
from daengs_place.place.planning.purpose import PurposeId, resolve_purposes


class HypothesisMappingScope(StrEnum):
    DIRECT = "direct"
    COMPOSED = "composed"
    EXPANDED = "expanded"
    PRODUCT_POLICY = "product_policy"


class ModifierExecution(StrEnum):
    RANK_ONLY_UNAVAILABLE = "rank_only_unavailable"


class NormalizationReceipt(PlanningModel):
    """어떤 입력 관찰에 어떤 고정 정책을 적용했는지 남기는 영수증."""

    policy_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_.:-]+$")
    input_observation_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    output_keys: tuple[str, ...] = Field(min_length=1, max_length=10)


class SearchModifier(PlanningModel):
    modifier_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_.:-]+$")
    execution: ModifierExecution
    basis_observation_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    required: bool = False


class UnresolvedFacet(PlanningModel):
    facet_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_.:-]+$")
    basis_observation_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    options: tuple[str, ...] = Field(min_length=1, max_length=10)
    blocking: bool


class SearchHypothesis(PlanningModel):
    hypothesis_key: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9_.:-]+$",
    )
    mapping_scope: HypothesisMappingScope
    targets: tuple[IntentObservation, ...] = Field(min_length=1, max_length=6)
    basis_observation_ids: tuple[str, ...] = Field(max_length=20)
    policy_id: str | None = Field(None, max_length=120, pattern=r"^[a-z0-9_.:-]+$")
    policy_version: str | None = Field(None, max_length=40, pattern=r"^[a-z0-9_.:-]+$")
    policy_branch_id: str | None = Field(None, max_length=80, pattern=r"^[a-z0-9-]+$")
    relation_receipts: tuple[NormalizationReceipt, ...] = ()

    @model_validator(mode="after")
    def targets_are_executable_place_targets(self) -> Self:
        policy_fields = (self.policy_id, self.policy_version, self.policy_branch_id)
        if any(value is None for value in policy_fields) != all(
            value is None for value in policy_fields
        ):
            raise ValueError("hypothesis policy provenance must be supplied together")
        if not self.basis_observation_ids and self.policy_id is None:
            raise ValueError("hypothesis requires observation or product policy provenance")
        for target in self.targets:
            if target.role is not IntentRole.REQUIRED_TARGET or not isinstance(
                target.intent, (KindIntent, PurposeIntent)
            ):
                raise ValueError("search hypothesis targets must be executable place targets")
        return self


class SearchHypothesisSet(PlanningModel):
    hypothesis_set_key: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9_.:-]+$",
    )
    search_directive: GroundedSearchDirective = Field(default_factory=GroundedSearchDirective)
    common: tuple[IntentObservation, ...] = Field(max_length=20)
    hypotheses: tuple[SearchHypothesis, ...] = Field(max_length=5)
    modifiers: tuple[SearchModifier, ...] = Field(max_length=10)
    unresolved_facets: tuple[UnresolvedFacet, ...] = Field(max_length=10)
    relation_receipts: tuple[NormalizationReceipt, ...] = Field(max_length=20)

    @model_validator(mode="after")
    def keys_and_common_ids_are_unique(self) -> Self:
        keys = [item.hypothesis_key for item in self.hypotheses]
        if len(keys) != len(set(keys)):
            raise ValueError("search hypothesis keys must be unique")
        common_ids = [item.observation_id for item in self.common]
        if len(common_ids) != len(set(common_ids)):
            raise ValueError("common observation ids must be unique")
        return self

    def planner_observations(
        self,
        hypothesis: SearchHypothesis,
    ) -> tuple[IntentObservation, ...]:
        """후속 executor가 공통 조건을 빠뜨리지 않도록 한 조립 경로만 제공한다."""

        if hypothesis not in self.hypotheses:
            raise ValueError("hypothesis does not belong to this set")
        return (*hypothesis.targets, *self.common)


class NormalizedIntentOutput(PlanningModel):
    source_disposition: ProposalDisposition
    hypothesis_sets: tuple[SearchHypothesisSet, ...] = Field(max_length=3)


_QUIET_CONCEPT = "semantic.quiet"
_CHEAP_CONCEPT = "semantic.cheap"
_DOG_INTEREST_CONCEPT = "semantic.dog_interest"
_COST_OPTIONS = (
    "cost.travel_distance",
    "cost.pet_fee",
    "cost.admission",
    "cost.product_price",
)
_BLOCKING_ROLES = {
    IntentRole.GOAL,
    IntentRole.REQUIRED_TARGET,
    IntentRole.REQUIRED_CONDITION,
    IntentRole.EXCLUDED,
}
_POSITIVE_COMPOSITION_ROLES = {
    IntentRole.GOAL,
    IntentRole.REQUIRED_TARGET,
    IntentRole.REQUIRED_CONDITION,
    IntentRole.PREFERENCE,
}


def _derived_target(
    interpretation_index: int,
    suffix: str,
    intent: KindIntent | PurposeIntent,
) -> IntentObservation:
    return observe_intent(
        IntentProposal(role=IntentRole.REQUIRED_TARGET, intent=intent),
        IntentSource.RULE_INFERENCE,
        observation_id=f"hypothesis-{interpretation_index}-{suffix}",
    )


def _receipt(
    policy_id: str,
    observations: tuple[IntentObservation, ...],
    *output_keys: str,
) -> NormalizationReceipt:
    return NormalizationReceipt(
        policy_id=policy_id,
        input_observation_ids=tuple(item.observation_id for item in observations),
        output_keys=output_keys,
    )


def _is_target(observation: IntentObservation) -> bool:
    return observation.role is IntentRole.REQUIRED_TARGET and isinstance(
        observation.intent, (KindIntent, PurposeIntent)
    )


def _narrow_targets(
    targets: list[IntentObservation],
) -> tuple[tuple[IntentObservation, ...], tuple[NormalizationReceipt, ...]]:
    """같은 해석의 구체 kind가 포괄 purpose보다 좁을 때만 중복을 제거한다."""

    kind_targets = [item for item in targets if isinstance(item.intent, KindIntent)]
    retained: list[IntentObservation] = []
    receipts: list[NormalizationReceipt] = []
    for target in targets:
        if not isinstance(target.intent, PurposeIntent):
            retained.append(target)
            continue
        covered = tuple(
            item
            for item in kind_targets
            if item.intent.kind in resolve_purposes([target.intent.purpose_id]).kinds
        )
        if not covered:
            retained.append(target)
            continue
        receipts.append(
            _receipt(
                "target.narrower_kind_over_purpose",
                (target, *covered),
                *(item.observation_id for item in covered),
            )
        )
    return tuple(retained), tuple(receipts)


def _build_set(
    observations: tuple[IntentObservation, ...],
    *,
    interpretation_index: int,
    search_directive: GroundedSearchDirective | None = None,
) -> SearchHypothesisSet:
    set_key = f"interpretation:{interpretation_index}"
    targets = [item for item in observations if _is_target(item)]
    consumed_ids: set[str] = {item.observation_id for item in targets}
    modifiers: list[SearchModifier] = []
    unresolved: list[UnresolvedFacet] = []
    set_receipts: list[NormalizationReceipt] = []

    quiet = tuple(
        item
        for item in observations
        if isinstance(item.intent, SemanticIntent)
        and item.intent.concept_id == _QUIET_CONCEPT
        and item.role not in {IntentRole.NEGATED, IntentRole.EXCLUDED}
    )
    if quiet:
        consumed_ids.update(item.observation_id for item in quiet)
        modifiers.append(
            SearchModifier(
                modifier_id=_QUIET_CONCEPT,
                execution=ModifierExecution.RANK_ONLY_UNAVAILABLE,
                basis_observation_ids=tuple(item.observation_id for item in quiet),
                required=any(item.role in _BLOCKING_ROLES for item in quiet),
            )
        )
        set_receipts.append(_receipt("semantic.quiet_to_rank_modifier", quiet, _QUIET_CONCEPT))

    dog_interest = tuple(
        item
        for item in observations
        if isinstance(item.intent, SemanticIntent)
        and item.intent.concept_id == _DOG_INTEREST_CONCEPT
        and item.role not in {IntentRole.NEGATED, IntentRole.EXCLUDED}
    )
    if dog_interest:
        consumed_ids.update(item.observation_id for item in dog_interest)
        modifiers.append(
            SearchModifier(
                modifier_id=_DOG_INTEREST_CONCEPT,
                execution=ModifierExecution.RANK_ONLY_UNAVAILABLE,
                basis_observation_ids=tuple(item.observation_id for item in dog_interest),
                required=any(item.role in _BLOCKING_ROLES for item in dog_interest),
            )
        )
        set_receipts.append(
            _receipt(
                "semantic.dog_interest_to_rank_modifier",
                dog_interest,
                _DOG_INTEREST_CONCEPT,
            )
        )

    cheap = tuple(
        item
        for item in observations
        if isinstance(item.intent, SemanticIntent)
        and item.intent.concept_id == _CHEAP_CONCEPT
        and item.role not in {IntentRole.NEGATED, IntentRole.EXCLUDED}
    )
    if cheap:
        consumed_ids.update(item.observation_id for item in cheap)
        unresolved.append(
            UnresolvedFacet(
                facet_id="cost.dimension",
                basis_observation_ids=tuple(item.observation_id for item in cheap),
                options=_COST_OPTIONS,
                blocking=any(item.role in _BLOCKING_ROLES for item in cheap),
            )
        )
        set_receipts.append(_receipt("semantic.cheap_requires_dimension", cheap, "cost.dimension"))

    buys = tuple(
        item
        for item in observations
        if isinstance(item.intent, ActivityIntent)
        and item.intent.activity_id is ActivityId.BUY
        and item.role in _POSITIVE_COMPOSITION_ROLES
    )
    dog_toys = tuple(
        item
        for item in observations
        if isinstance(item.intent, ObjectIntent)
        and item.intent.object_id is SearchObjectId.DOG_TOY
        and item.role in _POSITIVE_COMPOSITION_ROLES
    )
    composition_receipts: list[NormalizationReceipt] = []
    if buys and dog_toys:
        composition_inputs = (*buys, *dog_toys)
        consumed_ids.update(item.observation_id for item in composition_inputs)
        pet_shop_target = next(
            (
                item
                for item in targets
                if isinstance(item.intent, KindIntent) and item.intent.kind is PlaceKind.PET_SHOP
            ),
            None,
        )
        if pet_shop_target is None and not targets:
            targets.append(
                _derived_target(
                    interpretation_index,
                    "buy-dog-toy-pet-shop",
                    KindIntent(kind=PlaceKind.PET_SHOP),
                )
            )
            pet_shop_target = targets[-1]
        if pet_shop_target is not None:
            composition_receipts.append(
                _receipt(
                    "activity.buy_dog_toy_implies_pet_shop",
                    composition_inputs,
                    pet_shop_target.observation_id,
                )
            )
        else:
            modifiers.append(
                SearchModifier(
                    modifier_id="composition.buy_dog_toy",
                    execution=ModifierExecution.RANK_ONLY_UNAVAILABLE,
                    basis_observation_ids=tuple(item.observation_id for item in composition_inputs),
                    required=any(item.role in _BLOCKING_ROLES for item in composition_inputs),
                )
            )
            composition_receipts.append(
                _receipt(
                    "activity.buy_dog_toy_kept_with_target",
                    composition_inputs,
                    *(item.observation_id for item in targets),
                )
            )

    narrowed_targets, narrowing_receipts = _narrow_targets(targets)
    relation_receipts = (*composition_receipts, *narrowing_receipts)
    hypotheses: list[SearchHypothesis] = []
    if narrowed_targets:
        source_target_ids = tuple(item.observation_id for item in observations if _is_target(item))
        composed_ids = tuple(item.observation_id for item in (*buys, *dog_toys))
        basis_ids = tuple(dict.fromkeys((*source_target_ids, *composed_ids)))
        hypotheses.append(
            SearchHypothesis(
                hypothesis_key=f"{set_key}:target",
                mapping_scope=(
                    HypothesisMappingScope.COMPOSED
                    if composition_receipts
                    else HypothesisMappingScope.DIRECT
                ),
                targets=narrowed_targets,
                basis_observation_ids=basis_ids,
                relation_receipts=relation_receipts,
            )
        )

    directive = search_directive or GroundedSearchDirective()
    if directive.mode is SearchModeId.OPEN_DISCOVERY and not hypotheses:
        for branch in OPEN_DISCOVERY_POLICY.branches:
            hypothesis_key = f"{set_key}:open-discovery:{branch.branch_id}"
            hypotheses.append(
                SearchHypothesis(
                    hypothesis_key=hypothesis_key,
                    mapping_scope=HypothesisMappingScope.PRODUCT_POLICY,
                    targets=(
                        _derived_target(
                            interpretation_index,
                            f"open-discovery-{branch.branch_id}",
                            PurposeIntent(purpose_id=branch.purpose_id),
                        ),
                    ),
                    basis_observation_ids=(),
                    policy_id=OPEN_DISCOVERY_POLICY.policy_id,
                    policy_version=OPEN_DISCOVERY_POLICY.version,
                    policy_branch_id=branch.branch_id,
                )
            )

    plays = tuple(
        item
        for item in observations
        if isinstance(item.intent, ActivityIntent)
        and item.intent.activity_id is ActivityId.PLAY
        and item.role
        in {
            IntentRole.GOAL,
            IntentRole.REQUIRED_TARGET,
            IntentRole.REQUIRED_CONDITION,
            IntentRole.PREFERENCE,
        }
    )
    if plays:
        consumed_ids.update(item.observation_id for item in plays)
        if hypotheses:
            modifiers.append(
                SearchModifier(
                    modifier_id="activity.play",
                    execution=ModifierExecution.RANK_ONLY_UNAVAILABLE,
                    basis_observation_ids=tuple(item.observation_id for item in plays),
                    required=True,
                )
            )
            set_receipts.append(
                _receipt("activity.play_kept_as_target_modifier", plays, "activity.play")
            )
        else:
            play_targets = (
                (
                    "play:dedicated",
                    "dedicated-play",
                    KindIntent(kind=PlaceKind.LEISURE),
                ),
                (
                    "play:outdoor",
                    "outdoor-play",
                    KindIntent(kind=PlaceKind.TRAVEL),
                ),
                (
                    "play:stay-together",
                    "stay-together",
                    PurposeIntent(purpose_id=PurposeId.DINING),
                ),
            )
            for branch_key, suffix, intent in play_targets:
                hypothesis_key = f"{set_key}:{branch_key}"
                target = _derived_target(interpretation_index, suffix, intent)
                receipt = _receipt(
                    f"activity.play_expands_to_{branch_key.split(':', 1)[1].replace('-', '_')}",
                    plays,
                    hypothesis_key,
                )
                hypotheses.append(
                    SearchHypothesis(
                        hypothesis_key=hypothesis_key,
                        mapping_scope=HypothesisMappingScope.EXPANDED,
                        targets=(target,),
                        basis_observation_ids=tuple(item.observation_id for item in plays),
                        relation_receipts=(receipt,),
                    )
                )

    common = tuple(item for item in observations if item.observation_id not in consumed_ids)
    return SearchHypothesisSet(
        hypothesis_set_key=set_key,
        search_directive=directive,
        common=common,
        hypotheses=tuple(hypotheses),
        modifiers=tuple(modifiers),
        unresolved_facets=tuple(unresolved),
        relation_receipts=tuple(set_receipts),
    )


def build_search_hypotheses(output: MaterializedIntentOutput) -> NormalizedIntentOutput:
    """각 interpretation을 합치지 않고 독립적인 가설 집합으로 정규화한다."""

    if output.disposition is ProposalDisposition.ABSTAINED:
        return NormalizedIntentOutput(
            source_disposition=output.disposition,
            hypothesis_sets=(),
        )
    return NormalizedIntentOutput(
        source_disposition=output.disposition,
        hypothesis_sets=tuple(
            _build_set(
                interpretation.observations,
                interpretation_index=index,
                search_directive=interpretation.search_directive,
            )
            for index, interpretation in enumerate(output.interpretations, start=1)
        ),
    )
