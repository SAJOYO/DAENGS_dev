from collections.abc import Iterator

import pytest

from daengs_place.place.information_needs import InformationNeedId
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
from daengs_place.place.intent.hypotheses import build_search_hypotheses
from daengs_place.place.intent.lenses import (
    FacetOptionAvailability,
    LensAvailability,
    LensMappingScope,
    LensType,
    compile_search_lenses,
)
from daengs_place.place.intent.refinement import resolve_search_facet
from daengs_place.place.intent.suggestions import compile_intent_suggestions
from daengs_place.place.planning.contract import CapabilityId, PlaceKind
from daengs_place.place.planning.intents import (
    ActivityId,
    ActivityIntent,
    BooleanCapabilityIntent,
    IntentRole,
    KindIntent,
    ObjectIntent,
    PlannerStatus,
    SearchObjectId,
    SemanticIntent,
)

_SPATIAL = {"lat": 37.5563, "lng": 126.9236, "radius_m": 3000}


def _ids() -> Iterator[str]:
    index = 0
    while True:
        index += 1
        yield f"lens-source-{index}"


def _proposal(role: IntentRole, intent, quote: str) -> LLMIntentProposal:
    return LLMIntentProposal(
        role=role,
        intent=intent,
        evidence=EvidenceQuote(quote=quote, start=None, end=None),
    )


def _compile(utterance: str, *proposals: LLMIntentProposal):
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
    normalized = build_search_hypotheses(grounded)
    suggestions = compile_intent_suggestions(
        grounded,
        spatial=_SPATIAL,
        limit_per_kind=3,
    )
    return compile_search_lenses(
        normalized,
        suggestions,
        spatial=_SPATIAL,
        limit_per_kind=3,
    )


def _compile_open(utterance: str, *proposals: LLMIntentProposal):
    ids = _ids()
    grounded = materialize_llm_output(
        utterance,
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
                    proposals=proposals,
                ),
            ),
            reason=None,
        ),
        id_factory=lambda: next(ids),
    )
    normalized = build_search_hypotheses(grounded)
    suggestions = compile_intent_suggestions(
        grounded,
        spatial=_SPATIAL,
        limit_per_kind=3,
    )
    return compile_search_lenses(
        normalized,
        suggestions,
        spatial=_SPATIAL,
        limit_per_kind=3,
    )


def test_open_discovery_policy_exposes_three_transparent_executable_lenses() -> None:
    lenses = _compile_open("오늘 심심한데 네가 추천해봐")

    assert [item.display_label for item in lenses.target_lenses] == [
        "#먹고 쉬기",
        "#가볍게 나가기",
        "#구경하기",
    ]
    assert all(
        item.mapping_scope is LensMappingScope.OPEN_DISCOVERY for item in lenses.target_lenses
    )
    assert all(item.availability is LensAvailability.EXECUTABLE for item in lenses.target_lenses)
    assert all(
        item.candidate.basis_policy_id == "place.open_discovery" for item in lenses.target_lenses
    )
    assert all(item.candidate.basis_policy_version == "v1" for item in lenses.target_lenses)
    assert all("보장하지 않습니다" in item.support_note for item in lenses.target_lenses)


def test_open_discovery_preserves_hard_parking_without_relaxing_any_branch() -> None:
    lenses = _compile_open(
        "주차는 꼭 돼야 하고 네가 추천해봐",
        _proposal(
            IntentRole.REQUIRED_CONDITION,
            BooleanCapabilityIntent(
                capability_id=CapabilityId.OPERATIONS_PARKING,
                value=True,
            ),
            "주차는 꼭 돼야 하고",
        ),
    )

    assert len(lenses.target_lenses) == 3
    assert all(item.availability is LensAvailability.BLOCKED for item in lenses.target_lenses)
    assert all(
        "unsupported_capability_strength" in item.unsupported_signals
        for item in lenses.target_lenses
    )
    assert not lenses.executable_targets


def test_open_discovery_does_not_drop_an_explicit_exclusion_to_get_results() -> None:
    lenses = _compile_open(
        "카페 말고 네가 추천해봐",
        _proposal(
            IntentRole.EXCLUDED,
            KindIntent(kind=PlaceKind.CAFE),
            "카페 말고",
        ),
    )

    assert len(lenses.target_lenses) == 3
    assert all(item.availability is LensAvailability.BLOCKED for item in lenses.target_lenses)
    assert not lenses.executable_targets


def test_play_hypotheses_become_three_executable_broad_lenses() -> None:
    lenses = _compile(
        "강아지랑 놀고 싶음",
        _proposal(
            IntentRole.GOAL,
            ActivityIntent(activity_id=ActivityId.PLAY),
            "놀고 싶음",
        ),
    )

    assert [item.display_label for item in lenses.target_lenses] == [
        "#놀기",
        "#산책·야외",
        "#같이 쉬기",
    ]
    assert all(item.mapping_scope is LensMappingScope.BROAD for item in lenses.target_lenses)
    assert all(item.availability is LensAvailability.EXECUTABLE for item in lenses.target_lenses)
    assert all(item.candidate.result.status is PlannerStatus.READY for item in lenses.target_lenses)
    assert all(
        item.information_need_ids == (InformationNeedId.ACTIVITY_PLAY,)
        for item in lenses.target_lenses
    )
    assert lenses.executable_targets == lenses.target_lenses


def test_buying_dog_toy_exposes_pet_shop_without_inventory_overclaim() -> None:
    lenses = _compile(
        "강아지 장난감 사고 싶어 펫샵",
        _proposal(
            IntentRole.GOAL,
            ActivityIntent(activity_id=ActivityId.BUY),
            "사고 싶어",
        ),
        _proposal(
            IntentRole.GOAL,
            ObjectIntent(object_id=SearchObjectId.DOG_TOY),
            "강아지 장난감",
        ),
        _proposal(
            IntentRole.REQUIRED_TARGET,
            KindIntent(kind=PlaceKind.PET_SHOP),
            "펫샵",
        ),
    )

    assert len(lenses.target_lenses) == 1
    lens = lenses.target_lenses[0]
    assert lens.display_label == "#펫샵"
    assert lens.mapping_scope is LensMappingScope.DIRECT
    assert lens.confirmable_targets == (KindIntent(kind=PlaceKind.PET_SHOP),)
    assert lens.information_need_ids == (InformationNeedId.PRODUCTS_PURCHASABLE,)
    assert "판매·재고는 보장하지 않습니다" in lens.support_note


def test_buying_dog_toy_keeps_an_existing_target_and_required_purchase_signal() -> None:
    lenses = _compile(
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

    assert [item.display_label for item in lenses.target_lenses] == ["#쇼핑"]
    assert lenses.target_lenses[0].confirmable_targets == (KindIntent(kind=PlaceKind.SHOPPING),)
    assert "판매·재고는 보장하지 않습니다" in lenses.target_lenses[0].support_note
    assert lenses.signal_lenses[0].display_label == "#강아지 장난감 구매"
    assert lenses.signal_lenses[0].required is True


def test_quiet_fallbacks_are_executable_but_disclose_missing_ranking() -> None:
    lenses = _compile(
        "조용한 곳",
        _proposal(
            IntentRole.REQUIRED_CONDITION,
            SemanticIntent(concept_id="semantic.quiet"),
            "조용한 곳",
        ),
    )

    assert [item.display_label for item in lenses.target_lenses] == [
        "#식사·카페",
        "#나들이",
        "#문화공간",
    ]
    assert all(
        item.mapping_scope is LensMappingScope.PRODUCT_FALLBACK for item in lenses.target_lenses
    )
    assert all(item.availability is LensAvailability.EXECUTABLE for item in lenses.target_lenses)
    assert all(
        "조용함을 확인한 결과가 아니라" in item.support_note for item in lenses.target_lenses
    )
    signal = lenses.signal_lenses[0]
    assert signal.lens_type is LensType.MODIFIER
    assert signal.display_label == "#조용한 분위기"
    assert signal.availability is LensAvailability.DEFERRED


def test_dog_interest_fallbacks_show_results_without_claiming_preference_evidence() -> None:
    lenses = _compile(
        "강아지가 좋아하는 거 있는 곳",
        _proposal(
            IntentRole.PREFERENCE,
            SemanticIntent(concept_id="semantic.dog_interest"),
            "강아지가 좋아하는 거 있는 곳",
        ),
    )

    assert len(lenses.executable_targets) == 3
    assert all(
        item.mapping_scope is LensMappingScope.PRODUCT_FALLBACK for item in lenses.target_lenses
    )
    assert lenses.signal_lenses[0].display_label == "#강아지 관심 가능성"
    assert "판정할 장소 evidence" in lenses.signal_lenses[0].support_note


def test_cheap_preference_shows_results_while_asking_which_cost_dimension() -> None:
    lenses = _compile(
        "싸게 갈 수 있는 곳",
        _proposal(
            IntentRole.PREFERENCE,
            SemanticIntent(concept_id="semantic.cheap"),
            "싸게 갈 수 있는 곳",
        ),
    )

    assert len(lenses.executable_targets) == 3
    assert all(item.availability is LensAvailability.EXECUTABLE for item in lenses.target_lenses)
    assert lenses.signal_lenses[0].lens_type is LensType.UNRESOLVED
    assert lenses.signal_lenses[0].availability is LensAvailability.NEEDS_SELECTION


def test_cheap_becomes_a_non_executing_cost_question_with_honest_options() -> None:
    lenses = _compile(
        "싸게 갈 수 있는 곳",
        _proposal(
            IntentRole.REQUIRED_CONDITION,
            SemanticIntent(concept_id="semantic.cheap"),
            "싸게 갈 수 있는 곳",
        ),
    )

    assert len(lenses.target_lenses) == 3
    assert all(
        item.availability is LensAvailability.NEEDS_SELECTION for item in lenses.target_lenses
    )
    assert not lenses.executable_targets
    signal = lenses.signal_lenses[0]
    assert signal.lens_type is LensType.UNRESOLVED
    assert signal.availability is LensAvailability.NEEDS_SELECTION
    assert [item.display_label for item in signal.options] == [
        "가까운 곳",
        "반려견 추가요금",
        "입장료",
        "물건값",
    ]
    assert signal.options[0].availability is FacetOptionAvailability.PROXY
    assert all(
        item.availability is FacetOptionAvailability.UNAVAILABLE for item in signal.options[1:]
    )


def test_blocking_cost_facet_prevents_an_otherwise_ready_target_from_execution() -> None:
    lenses = _compile(
        "싼 카페",
        _proposal(
            IntentRole.REQUIRED_TARGET,
            KindIntent(kind=PlaceKind.CAFE),
            "카페",
        ),
        _proposal(
            IntentRole.REQUIRED_CONDITION,
            SemanticIntent(concept_id="semantic.cheap"),
            "싼",
        ),
    )

    lens = lenses.target_lenses[0]
    assert lens.candidate.result.status is PlannerStatus.READY
    assert lens.availability is LensAvailability.NEEDS_SELECTION
    assert lens.unresolved_facet_ids == ("cost.dimension",)
    assert not lenses.executable_targets

    refined = resolve_search_facet(
        lenses,
        signal_lens_id=lenses.signal_lenses[0].lens_id,
        option_id="cost.travel_distance",
    )

    assert refined.target_lenses[0].availability is LensAvailability.EXECUTABLE
    assert not refined.target_lenses[0].unresolved_facet_ids
    assert refined.signal_lenses[0].availability is LensAvailability.RESOLVED
    assert refined.signal_lenses[0].selected_option_id == "cost.travel_distance"
    assert "실제 가격이 아니라" in refined.target_lenses[0].support_note


def test_unavailable_cost_facet_cannot_open_a_target_lens() -> None:
    lenses = _compile(
        "싼 카페",
        _proposal(IntentRole.REQUIRED_TARGET, KindIntent(kind=PlaceKind.CAFE), "카페"),
        _proposal(
            IntentRole.REQUIRED_CONDITION,
            SemanticIntent(concept_id="semantic.cheap"),
            "싼",
        ),
    )

    with pytest.raises(ValueError, match="not executable"):
        resolve_search_facet(
            lenses,
            signal_lens_id=lenses.signal_lenses[0].lens_id,
            option_id="cost.product_price",
        )


def test_optional_cost_facet_can_be_resolved_without_blocking_existing_targets() -> None:
    lenses = _compile(
        "싸면 좋은 곳",
        _proposal(
            IntentRole.PREFERENCE,
            SemanticIntent(concept_id="semantic.cheap"),
            "싸면 좋은 곳",
        ),
    )
    assert lenses.executable_targets

    refined = resolve_search_facet(
        lenses,
        signal_lens_id=lenses.signal_lenses[0].lens_id,
        option_id="cost.travel_distance",
    )

    assert [item.lens_id for item in refined.executable_targets] == [
        item.lens_id for item in lenses.executable_targets
    ]
    assert refined.signal_lenses[0].availability is LensAvailability.RESOLVED
    assert all("실제 가격이 아니라" in item.support_note for item in refined.target_lenses)


def test_common_hard_condition_blocks_every_expanded_play_lens() -> None:
    lenses = _compile(
        "놀고 싶고 주차는 꼭 돼야 해",
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

    assert len(lenses.target_lenses) == 3
    assert all(item.availability is LensAvailability.BLOCKED for item in lenses.target_lenses)
    assert all(
        "unsupported_capability_strength" in item.unsupported_signals
        for item in lenses.target_lenses
    )
    assert not lenses.executable_targets


def test_fallback_lenses_only_receive_signals_from_their_interpretation() -> None:
    ids = _ids()
    utterance = "조용하거나 싼 곳"
    grounded = materialize_llm_output(
        utterance,
        LLMIntentOutput(
            disposition=ProposalDisposition.AMBIGUOUS,
            interpretations=(
                IntentInterpretation(
                    proposals=(
                        _proposal(
                            IntentRole.REQUIRED_CONDITION,
                            SemanticIntent(concept_id="semantic.quiet"),
                            "조용",
                        ),
                    )
                ),
                IntentInterpretation(
                    proposals=(
                        _proposal(
                            IntentRole.REQUIRED_CONDITION,
                            SemanticIntent(concept_id="semantic.cheap"),
                            "싼",
                        ),
                    )
                ),
            ),
            reason=ProposalReason.MULTIPLE_PLAUSIBLE_READINGS,
        ),
        id_factory=lambda: next(ids),
    )
    normalized = build_search_hypotheses(grounded)
    suggestions = compile_intent_suggestions(
        grounded,
        spatial=_SPATIAL,
        limit_per_kind=3,
    )

    lenses = compile_search_lenses(
        normalized,
        suggestions,
        spatial=_SPATIAL,
        limit_per_kind=3,
    )

    assert len(lenses.target_lenses) == 6
    quiet, cheap = lenses.target_lenses[:3], lenses.target_lenses[3:]
    assert all(item.availability is LensAvailability.EXECUTABLE for item in quiet)
    assert all(not item.unresolved_facet_ids for item in quiet)
    assert all(item.modifier_ids == ("semantic.quiet",) for item in quiet)
    assert all(item.availability is LensAvailability.NEEDS_SELECTION for item in cheap)
    assert all(item.unresolved_facet_ids == ("cost.dimension",) for item in cheap)

    cost_signal = next(
        item
        for item in lenses.signal_lenses
        if item.lens_id.startswith("signal:interpretation:2:facet:")
    )
    refined = resolve_search_facet(
        lenses,
        signal_lens_id=cost_signal.lens_id,
        option_id="cost.travel_distance",
    )
    assert all("실제 가격이 아니라" not in item.support_note for item in refined.target_lenses[:3])
    assert all("실제 가격이 아니라" in item.support_note for item in refined.target_lenses[3:])


def test_signal_lens_preserves_required_modifier_strength() -> None:
    required = _compile(
        "조용한 카페",
        _proposal(
            IntentRole.REQUIRED_TARGET,
            KindIntent(kind=PlaceKind.CAFE),
            "카페",
        ),
        _proposal(
            IntentRole.REQUIRED_CONDITION,
            SemanticIntent(concept_id="semantic.quiet"),
            "조용한",
        ),
    )
    optional = _compile(
        "조용하면 좋은 카페",
        _proposal(
            IntentRole.REQUIRED_TARGET,
            KindIntent(kind=PlaceKind.CAFE),
            "카페",
        ),
        _proposal(
            IntentRole.PREFERENCE,
            SemanticIntent(concept_id="semantic.quiet"),
            "조용하면 좋은",
        ),
    )

    assert required.signal_lenses[0].required is True
    assert optional.signal_lenses[0].required is False
    assert "필수로 요청한" in required.signal_lenses[0].support_note
