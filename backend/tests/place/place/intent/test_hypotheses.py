from collections.abc import Iterator

import pytest

from daengs_place.place.intent.contract import (
    EvidenceQuote,
    IntentInterpretation,
    LLMIntentOutput,
    LLMIntentProposal,
    LLMSearchDirective,
    ProposalDisposition,
    ProposalReason,
    SearchModeId,
    materialize_llm_output,
)
from daengs_place.place.intent.hypotheses import (
    HypothesisMappingScope,
    ModifierExecution,
    build_search_hypotheses,
)
from daengs_place.place.planning.contract import CapabilityId, PlaceKind
from daengs_place.place.planning.intents import (
    ActivityId,
    ActivityIntent,
    BooleanCapabilityIntent,
    IntentRole,
    IntentSource,
    KindIntent,
    ObjectIntent,
    PlannerRequest,
    PlannerStatus,
    PurposeIntent,
    SearchObjectId,
    SemanticIntent,
)
from daengs_place.place.planning.planner import compile_intent_plan
from daengs_place.place.planning.purpose import PurposeId

_SPATIAL = {"lat": 37.5, "lng": 127.0, "radius_m": 3000}


def _ids() -> Iterator[str]:
    index = 0
    while True:
        index += 1
        yield f"source-{index}"


def _proposal(role: IntentRole, intent, quote: str) -> LLMIntentProposal:
    return LLMIntentProposal(
        role=role,
        intent=intent,
        evidence=EvidenceQuote(quote=quote, start=None, end=None),
    )


def _normalize(utterance: str, *proposals: LLMIntentProposal):
    ids = _ids()
    grounded = materialize_llm_output(
        utterance,
        LLMIntentOutput(
            disposition=ProposalDisposition.PROPOSED,
            interpretations=(IntentInterpretation(proposals=proposals),),
            reason=None,
        ),
        id_factory=lambda: next(ids),
    )
    return build_search_hypotheses(grounded).hypothesis_sets[0]


def test_specific_pet_shop_target_removes_redundant_shopping_purpose() -> None:
    normalized = _normalize(
        "펫샵에서 쇼핑",
        _proposal(
            IntentRole.REQUIRED_TARGET,
            KindIntent(kind=PlaceKind.PET_SHOP),
            "펫샵",
        ),
        _proposal(
            IntentRole.REQUIRED_TARGET,
            PurposeIntent(purpose_id=PurposeId.SHOPPING),
            "쇼핑",
        ),
    )

    assert len(normalized.hypotheses) == 1
    hypothesis = normalized.hypotheses[0]
    assert hypothesis.mapping_scope is HypothesisMappingScope.DIRECT
    assert [target.intent for target in hypothesis.targets] == [KindIntent(kind=PlaceKind.PET_SHOP)]
    assert hypothesis.relation_receipts[0].policy_id == "target.narrower_kind_over_purpose"
    assert hypothesis.relation_receipts[0].input_observation_ids == (
        "source-2",
        "source-1",
    )


def test_open_discovery_directive_survives_normalization_without_becoming_common_intent() -> None:
    grounded = materialize_llm_output(
        "오늘 심심한데 네가 추천해봐",
        LLMIntentOutput(
            disposition=ProposalDisposition.PROPOSED,
            interpretations=(
                IntentInterpretation(
                    search_directive=LLMSearchDirective(
                        mode=SearchModeId.OPEN_DISCOVERY,
                        evidence=EvidenceQuote(
                            quote="네가 추천해봐",
                            start=None,
                            end=None,
                        ),
                    ),
                    proposals=(),
                ),
            ),
            reason=None,
        ),
    )

    hypothesis_set = build_search_hypotheses(grounded).hypothesis_sets[0]

    assert hypothesis_set.search_directive.mode is SearchModeId.OPEN_DISCOVERY
    assert hypothesis_set.common == ()
    assert [item.mapping_scope for item in hypothesis_set.hypotheses] == [
        HypothesisMappingScope.PRODUCT_POLICY,
    ] * 3
    assert [item.targets[0].intent for item in hypothesis_set.hypotheses] == [
        PurposeIntent(purpose_id=PurposeId.DINING),
        PurposeIntent(purpose_id=PurposeId.OUTING),
        PurposeIntent(purpose_id=PurposeId.CULTURE),
    ]
    assert all(
        item.targets[0].source is IntentSource.RULE_INFERENCE
        for item in hypothesis_set.hypotheses
    )
    assert all(item.basis_observation_ids == () for item in hypothesis_set.hypotheses)
    assert all(item.policy_id == "place.open_discovery" for item in hypothesis_set.hypotheses)
    assert all(item.policy_version == "v1" for item in hypothesis_set.hypotheses)
    assert all(
        hypothesis_set.planner_observations(item) == item.targets
        for item in hypothesis_set.hypotheses
    )


def test_open_discovery_policy_provenance_does_not_claim_common_conditions() -> None:
    ids = _ids()
    grounded = materialize_llm_output(
        "주차되면 좋고 네가 추천해봐",
        LLMIntentOutput(
            disposition=ProposalDisposition.PROPOSED,
            interpretations=(
                IntentInterpretation(
                    search_directive=LLMSearchDirective(
                        mode=SearchModeId.OPEN_DISCOVERY,
                        evidence=EvidenceQuote(
                            quote="네가 추천해봐",
                            start=None,
                            end=None,
                        ),
                    ),
                    proposals=(
                        _proposal(
                            IntentRole.PREFERENCE,
                            BooleanCapabilityIntent(
                                capability_id=CapabilityId.OPERATIONS_PARKING,
                                value=True,
                            ),
                            "주차되면 좋고",
                        ),
                    ),
                ),
            ),
            reason=None,
        ),
        id_factory=lambda: next(ids),
    )

    hypothesis_set = build_search_hypotheses(grounded).hypothesis_sets[0]

    assert [item.observation_id for item in hypothesis_set.common] == ["source-1"]
    assert all(item.basis_observation_ids == () for item in hypothesis_set.hypotheses)
    assert all(
        hypothesis_set.planner_observations(item) == (*item.targets, *hypothesis_set.common)
        for item in hypothesis_set.hypotheses
    )


def test_buy_and_dog_toy_compose_to_pet_shop_without_claiming_inventory() -> None:
    normalized = _normalize(
        "강아지 장난감 사고 싶어",
        _proposal(
            IntentRole.GOAL,
            ObjectIntent(object_id=SearchObjectId.DOG_TOY),
            "강아지 장난감",
        ),
        _proposal(
            IntentRole.GOAL,
            ActivityIntent(activity_id=ActivityId.BUY),
            "사고 싶어",
        ),
    )

    hypothesis = normalized.hypotheses[0]
    assert hypothesis.mapping_scope is HypothesisMappingScope.COMPOSED
    assert hypothesis.targets[0].intent == KindIntent(kind=PlaceKind.PET_SHOP)
    assert hypothesis.targets[0].source is IntentSource.RULE_INFERENCE
    assert hypothesis.relation_receipts[0].policy_id == ("activity.buy_dog_toy_implies_pet_shop")
    assert not normalized.common
    assert not normalized.unresolved_facets


def test_hypothetical_buy_and_dog_toy_do_not_create_a_positive_target() -> None:
    normalized = _normalize(
        "강아지 장난감을 산다면",
        _proposal(
            IntentRole.HYPOTHETICAL,
            ObjectIntent(object_id=SearchObjectId.DOG_TOY),
            "강아지 장난감",
        ),
        _proposal(
            IntentRole.HYPOTHETICAL,
            ActivityIntent(activity_id=ActivityId.BUY),
            "산다면",
        ),
    )

    assert not normalized.hypotheses
    assert [item.role for item in normalized.common] == [
        IntentRole.HYPOTHETICAL,
        IntentRole.HYPOTHETICAL,
    ]


def test_buying_dog_toy_does_not_widen_an_existing_place_target() -> None:
    normalized = _normalize(
        "쇼핑몰에서 강아지 장난감 사고 싶어",
        _proposal(
            IntentRole.REQUIRED_TARGET,
            KindIntent(kind=PlaceKind.SHOPPING),
            "쇼핑몰",
        ),
        _proposal(
            IntentRole.GOAL,
            ObjectIntent(object_id=SearchObjectId.DOG_TOY),
            "강아지 장난감",
        ),
        _proposal(
            IntentRole.GOAL,
            ActivityIntent(activity_id=ActivityId.BUY),
            "사고 싶어",
        ),
    )

    hypothesis = normalized.hypotheses[0]
    assert [item.intent for item in hypothesis.targets] == [KindIntent(kind=PlaceKind.SHOPPING)]
    assert normalized.modifiers[0].modifier_id == "composition.buy_dog_toy"
    assert normalized.modifiers[0].required is True
    assert hypothesis.relation_receipts[0].policy_id == ("activity.buy_dog_toy_kept_with_target")


@pytest.mark.parametrize(
    "role",
    [
        IntentRole.GOAL,
        IntentRole.REQUIRED_TARGET,
        IntentRole.REQUIRED_CONDITION,
        IntentRole.PREFERENCE,
    ],
)
def test_play_expands_to_three_independent_product_hypotheses(role: IntentRole) -> None:
    normalized = _normalize(
        "강아지랑 놀고 싶음",
        _proposal(
            role,
            ActivityIntent(activity_id=ActivityId.PLAY),
            "놀고 싶음",
        ),
    )

    assert [item.hypothesis_key for item in normalized.hypotheses] == [
        "interpretation:1:play:dedicated",
        "interpretation:1:play:outdoor",
        "interpretation:1:play:stay-together",
    ]
    assert all(
        item.mapping_scope is HypothesisMappingScope.EXPANDED for item in normalized.hypotheses
    )
    assert [item.targets[0].intent for item in normalized.hypotheses] == [
        KindIntent(kind=PlaceKind.LEISURE),
        KindIntent(kind=PlaceKind.TRAVEL),
        PurposeIntent(purpose_id=PurposeId.DINING),
    ]
    assert all(item.basis_observation_ids == ("source-1",) for item in normalized.hypotheses)


def test_quiet_is_preserved_as_unavailable_rank_only_modifier() -> None:
    normalized = _normalize(
        "조용한 곳",
        _proposal(
            IntentRole.REQUIRED_TARGET,
            SemanticIntent(concept_id="semantic.quiet"),
            "조용한 곳",
        ),
    )

    assert not normalized.hypotheses
    assert not normalized.common
    assert normalized.modifiers[0].modifier_id == "semantic.quiet"
    assert normalized.modifiers[0].execution is ModifierExecution.RANK_ONLY_UNAVAILABLE
    assert normalized.modifiers[0].required is True
    assert normalized.relation_receipts[0].policy_id == "semantic.quiet_to_rank_modifier"


def test_cheap_stays_unresolved_until_the_cost_dimension_is_selected() -> None:
    normalized = _normalize(
        "싸게 갈 수 있는 곳",
        _proposal(
            IntentRole.REQUIRED_TARGET,
            SemanticIntent(concept_id="semantic.cheap"),
            "싸게 갈 수 있는 곳",
        ),
    )

    assert not normalized.hypotheses
    facet = normalized.unresolved_facets[0]
    assert facet.facet_id == "cost.dimension"
    assert facet.blocking is True
    assert facet.options == (
        "cost.travel_distance",
        "cost.pet_fee",
        "cost.admission",
        "cost.product_price",
    )


def test_excluded_cheap_is_not_inverted_into_a_positive_cost_facet() -> None:
    normalized = _normalize(
        "싼 곳은 빼고 카페",
        _proposal(
            IntentRole.EXCLUDED,
            SemanticIntent(concept_id="semantic.cheap"),
            "싼 곳은 빼고",
        ),
        _proposal(
            IntentRole.REQUIRED_TARGET,
            KindIntent(kind=PlaceKind.CAFE),
            "카페",
        ),
    )

    assert not normalized.unresolved_facets
    assert [(item.role, item.intent) for item in normalized.common] == [
        (IntentRole.EXCLUDED, SemanticIntent(concept_id="semantic.cheap"))
    ]


def test_ambiguous_likings_keep_each_search_reading_in_a_separate_set() -> None:
    ids = _ids()
    utterance = "강아지가 좋아하는 거 있는 곳"
    grounded = materialize_llm_output(
        utterance,
        LLMIntentOutput(
            disposition=ProposalDisposition.AMBIGUOUS,
            interpretations=(
                IntentInterpretation(
                    proposals=(
                        _proposal(
                            IntentRole.GOAL,
                            ActivityIntent(activity_id=ActivityId.PLAY),
                            "좋아하는 거",
                        ),
                    )
                ),
                IntentInterpretation(
                    proposals=(
                        _proposal(
                            IntentRole.REQUIRED_TARGET,
                            PurposeIntent(purpose_id=PurposeId.DINING),
                            "좋아하는 거",
                        ),
                    )
                ),
                IntentInterpretation(
                    proposals=(
                        _proposal(
                            IntentRole.REQUIRED_TARGET,
                            KindIntent(kind=PlaceKind.PET_SHOP),
                            "좋아하는 거",
                        ),
                    )
                ),
            ),
            reason=ProposalReason.MULTIPLE_PLAUSIBLE_READINGS,
        ),
        id_factory=lambda: next(ids),
    )

    normalized = build_search_hypotheses(grounded)

    assert [item.hypothesis_set_key for item in normalized.hypothesis_sets] == [
        "interpretation:1",
        "interpretation:2",
        "interpretation:3",
    ]
    assert [len(item.hypotheses) for item in normalized.hypothesis_sets] == [3, 1, 1]
    assert normalized.hypothesis_sets[1].hypotheses[0].targets[0].intent == PurposeIntent(
        purpose_id=PurposeId.DINING
    )
    assert normalized.hypothesis_sets[2].hypotheses[0].targets[0].intent == KindIntent(
        kind=PlaceKind.PET_SHOP
    )


def test_common_hard_condition_is_assembled_into_every_play_branch() -> None:
    normalized = _normalize(
        "강아지랑 놀고 싶고 주차는 꼭 돼야 해",
        _proposal(
            IntentRole.GOAL,
            ActivityIntent(activity_id=ActivityId.PLAY),
            "놀고 싶고",
        ),
        _proposal(
            IntentRole.REQUIRED_CONDITION,
            BooleanCapabilityIntent(
                capability_id=CapabilityId.OPERATIONS_PARKING,
                value=True,
            ),
            "주차는 꼭 돼야 해",
        ),
    )

    assert [item.observation_id for item in normalized.common] == ["source-2"]
    for hypothesis in normalized.hypotheses:
        observations = normalized.planner_observations(hypothesis)
        assert [item.observation_id for item in observations][-1] == "source-2"
        result = compile_intent_plan(
            PlannerRequest(
                spatial=_SPATIAL,
                observations=observations,
                limit_per_kind=20,
            )
        )
        assert result.status is PlannerStatus.UNSUPPORTED
        assert result.unsupported[0].code == "unsupported_capability_strength"
        assert result.unsupported[0].blocking is True
