from collections.abc import Iterator

import pytest

from daengs_place.place.intent.contract import (
    EvidenceQuote,
    IntentInterpretation,
    IntentProposerInvalidOutputError,
    LLMIntentOutput,
    LLMIntentProposal,
    LLMSearchDirective,
    ProposalDisposition,
    ProposalReason,
    SearchModeId,
    materialize_llm_output,
)
from daengs_place.place.intent.service import PlaceIntentSuggestionService
from daengs_place.place.intent.suggestions import (
    IntentSuggestionOutcome,
    SuggestionBasis,
    SuggestionResolution,
    compile_intent_suggestions,
)
from daengs_place.place.planning.contract import CapabilityId, GateOrigin, PlaceKind
from daengs_place.place.planning.intents import (
    ActivityId,
    ActivityIntent,
    BooleanCapabilityIntent,
    IntentRole,
    KindIntent,
    ObjectIntent,
    PlannerIssue,
    PlannerStatus,
    PurposeIntent,
    SearchObjectId,
    SemanticIntent,
)
from daengs_place.place.planning.purpose import PurposeId

_SPATIAL = {"lat": 37.5, "lng": 127.0, "radius_m": 3000}


def _ids() -> Iterator[str]:
    index = 0
    while True:
        index += 1
        yield f"llm-test-{index}"


def _proposal(role: IntentRole, intent, quote: str) -> LLMIntentProposal:
    return LLMIntentProposal(
        role=role,
        intent=intent,
        evidence=EvidenceQuote(quote=quote, start=None, end=None),
    )


def _materialize(
    utterance: str,
    *interpretations: tuple[LLMIntentProposal, ...],
    disposition: ProposalDisposition = ProposalDisposition.PROPOSED,
):
    ids = _ids()
    return materialize_llm_output(
        utterance,
        LLMIntentOutput(
            disposition=disposition,
            interpretations=tuple(
                IntentInterpretation(proposals=items) for items in interpretations
            ),
            reason=(
                ProposalReason.MULTIPLE_PLAUSIBLE_READINGS
                if disposition is ProposalDisposition.AMBIGUOUS
                else None
            ),
        ),
        id_factory=lambda: next(ids),
    )


def _compile(output):
    return compile_intent_suggestions(
        output,
        spatial=_SPATIAL,
        limit_per_kind=20,
    )


def test_open_discovery_compiles_three_product_owned_exploration_candidates() -> None:
    output = materialize_llm_output(
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

    outcome = _compile(output)

    assert outcome.status is PlannerStatus.READY
    assert outcome.resolution is SuggestionResolution.EXPLORATORY
    assert len(outcome.suggestions) == 3
    assert all(item.basis is SuggestionBasis.OPEN_DISCOVERY for item in outcome.suggestions)
    assert all(item.basis_observation_ids == () for item in outcome.suggestions)
    assert all(item.basis_policy_id == "place.open_discovery" for item in outcome.suggestions)
    assert all(item.basis_policy_version == "v1" for item in outcome.suggestions)
    assert all(item.result.plan is not None for item in outcome.suggestions)
    purpose_gates = [
        next(
            gate
            for gate in item.result.plan.gates
            if gate.capability_id is CapabilityId.PURPOSE_KIND
        )
        for item in outcome.suggestions
        if item.result.plan is not None
    ]
    assert all(gate.origin is GateOrigin.INFERRED for gate in purpose_gates)
    assert all(not gate.locked and gate.relaxable for gate in purpose_gates)


async def test_service_keeps_directive_separate_while_exposing_product_lenses() -> None:
    raw = LLMIntentOutput(
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
    )
    service = PlaceIntentSuggestionService(_StaticProposer(raw))

    trace = await service.inspect(
        "오늘 심심한데 네가 추천해봐",
        spatial=_SPATIAL,
        limit_per_kind=20,
    )

    assert trace.grounded is not None
    assert trace.normalized is not None
    assert trace.normalized.hypothesis_sets[0].search_directive.mode is (
        SearchModeId.OPEN_DISCOVERY
    )
    assert trace.normalized.hypothesis_sets[0].common == ()
    assert trace.outcome.status is PlannerStatus.READY
    assert trace.outcome.resolution is SuggestionResolution.EXPLORATORY
    assert trace.lenses is not None
    assert [item.display_label for item in trace.lenses.target_lenses] == [
        "#먹고 쉬기",
        "#가볍게 나가기",
        "#구경하기",
    ]
    assert all(
        item.mapping_scope.value == "open_discovery"
        for item in trace.lenses.target_lenses
    )


def test_open_discovery_keeps_an_existing_composed_target_executable() -> None:
    ids = _ids()
    output = materialize_llm_output(
        "강아지 장난감 사고 싶어 네가 추천해줘",
        LLMIntentOutput(
            disposition=ProposalDisposition.PROPOSED,
            interpretations=(
                IntentInterpretation(
                    search_directive=LLMSearchDirective(
                        mode=SearchModeId.OPEN_DISCOVERY,
                        evidence=EvidenceQuote(
                            quote="네가 추천해줘",
                            start=None,
                            end=None,
                        ),
                    ),
                    proposals=(
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
                    ),
                ),
            ),
            reason=None,
        ),
        id_factory=lambda: next(ids),
    )

    outcome = _compile(output)

    assert outcome.status is PlannerStatus.READY
    assert outcome.resolution is SuggestionResolution.EXPLORATORY
    assert len(outcome.suggestions) == 1
    candidate = outcome.suggestions[0]
    assert candidate.basis is SuggestionBasis.HYPOTHESIS
    assert candidate.basis_observation_ids == ("llm-test-2", "llm-test-1")
    assert candidate.basis_policy_id is None
    assert candidate.result.plan is not None
    purpose = next(
        gate
        for gate in candidate.result.plan.gates
        if gate.capability_id is CapabilityId.PURPOSE_KIND
    )
    assert purpose.value == (PlaceKind.PET_SHOP,)


def test_single_literal_interpretation_becomes_one_inferred_unlocked_plan() -> None:
    output = _materialize(
        "식사할 곳",
        (
            _proposal(
                IntentRole.REQUIRED_TARGET,
                PurposeIntent(purpose_id=PurposeId.DINING),
                "식사할 곳",
            ),
        ),
    )

    outcome = _compile(output)

    assert outcome.status is PlannerStatus.READY
    assert outcome.resolution is SuggestionResolution.INFERRED
    assert len(outcome.suggestions) == 1 and not outcome.rejected
    candidate = outcome.suggestions[0]
    assert candidate.basis is SuggestionBasis.INTERPRETATION
    assert candidate.result.plan is not None
    purpose = candidate.result.plan.gates[0]
    assert purpose.value == (PlaceKind.CAFE, PlaceKind.RESTAURANT)
    assert purpose.origin is GateOrigin.INFERRED
    assert purpose.locked is False and purpose.relaxable is True


def test_ambiguous_interpretations_stay_as_separate_exploratory_plans() -> None:
    output = _materialize(
        "강아지랑 잠깐 머물 곳",
        (
            _proposal(
                IntentRole.REQUIRED_TARGET,
                PurposeIntent(purpose_id=PurposeId.DINING),
                "잠깐 머물 곳",
            ),
        ),
        (
            _proposal(
                IntentRole.REQUIRED_TARGET,
                PurposeIntent(purpose_id=PurposeId.OUTING),
                "잠깐 머물 곳",
            ),
        ),
        disposition=ProposalDisposition.AMBIGUOUS,
    )

    outcome = _compile(output)

    assert outcome.resolution is SuggestionResolution.EXPLORATORY
    assert [item.candidate_key for item in outcome.suggestions] == [
        "interpretation:1",
        "interpretation:2",
    ]
    values = [item.result.plan.gates[0].value for item in outcome.suggestions]
    assert values == [
        (PlaceKind.CAFE, PlaceKind.RESTAURANT),
        (PlaceKind.TRAVEL, PlaceKind.LEISURE),
    ]
    assert all(len(value) == 2 for value in values)


def test_analogy_uses_disclosed_product_fallback_not_literal_observation() -> None:
    output = _materialize(
        "카페 같은 분위기의 장소",
        (
            _proposal(
                IntentRole.ANALOGY,
                KindIntent(kind=PlaceKind.CAFE),
                "카페 같은",
            ),
            _proposal(
                IntentRole.REQUIRED_TARGET,
                SemanticIntent(concept_id="semantic.atmosphere"),
                "분위기의 장소",
            ),
        ),
    )

    outcome = _compile(output)

    assert outcome.status is PlannerStatus.READY
    assert outcome.resolution is SuggestionResolution.EXPLORATORY
    assert len(outcome.rejected) == 1
    # 운영 planner는 blocking semantic target을 unsupported로 분류한다. analogy에서
    # 만든 공개 fallback은 유지하되, 거절된 원 interpretation의 상태를 다시 쓰지 않는다.
    assert outcome.rejected[0].result.status is PlannerStatus.UNSUPPORTED
    assert outcome.rejected[0].result.unsupported[0].code == "non_literal_target"
    assert len(outcome.suggestions) == 1
    fallback = outcome.suggestions[0]
    assert fallback.basis is SuggestionBasis.PRODUCT_FALLBACK
    assert fallback.result.plan is not None
    assert fallback.result.plan.gates[0].value == (PlaceKind.CAFE,)
    assert fallback.result.plan.gates[0].origin is GateOrigin.INFERRED
    assert "분위기의 장소" not in fallback.result.plan.model_dump_json()


def test_allowlisted_soft_semantic_gets_default_leisure_groups_and_keeps_preference() -> None:
    output = _materialize(
        "조용하고 주차되면 좋겠어",
        (
            _proposal(
                IntentRole.REQUIRED_TARGET,
                SemanticIntent(concept_id="semantic.quiet"),
                "조용하고",
            ),
            _proposal(
                IntentRole.PREFERENCE,
                BooleanCapabilityIntent(
                    capability_id=CapabilityId.OPERATIONS_PARKING,
                    value=True,
                ),
                "주차되면 좋겠어",
            ),
        ),
    )

    outcome = _compile(output)

    assert outcome.resolution is SuggestionResolution.EXPLORATORY
    assert len(outcome.suggestions) == 3
    assert [item.candidate_key for item in outcome.suggestions] == [
        "interpretation:1:fallback:purpose:dining",
        "interpretation:1:fallback:purpose:outing",
        "interpretation:1:fallback:purpose:culture",
    ]
    assert all(item.result.plan is not None for item in outcome.suggestions)
    assert all(
        item.result.plan.gates[-1].capability_id is CapabilityId.OPERATIONS_PARKING
        for item in outcome.suggestions
    )
    assert outcome.rejected[0].result.unsupported[0].code == "unsupported_semantic_intent"


def test_cheap_without_a_target_offers_broad_groups_for_later_facet_selection() -> None:
    output = _materialize(
        "싸게 갈 수 있는 곳",
        (
            _proposal(
                IntentRole.REQUIRED_CONDITION,
                SemanticIntent(concept_id="semantic.cheap"),
                "싸게",
            ),
        ),
    )

    outcome = _compile(output)

    assert outcome.resolution is SuggestionResolution.EXPLORATORY
    assert [item.candidate_key for item in outcome.suggestions] == [
        "interpretation:1:fallback:purpose:dining",
        "interpretation:1:fallback:purpose:outing",
        "interpretation:1:fallback:purpose:culture",
    ]


def test_ambiguous_fallbacks_keep_each_interpretations_preferences_and_evidence() -> None:
    output = _materialize(
        "카페 같은 곳에 주차되면 좋고 아니면 나들이 갈까",
        (
            _proposal(
                IntentRole.ANALOGY,
                KindIntent(kind=PlaceKind.CAFE),
                "카페 같은",
            ),
            _proposal(
                IntentRole.PREFERENCE,
                BooleanCapabilityIntent(
                    capability_id=CapabilityId.OPERATIONS_PARKING,
                    value=True,
                ),
                "주차되면 좋고",
            ),
        ),
        (
            _proposal(
                IntentRole.HYPOTHETICAL,
                PurposeIntent(purpose_id=PurposeId.OUTING),
                "나들이 갈까",
            ),
        ),
        disposition=ProposalDisposition.AMBIGUOUS,
    )

    outcome = _compile(output)

    assert [item.candidate_key for item in outcome.suggestions] == [
        "interpretation:1:fallback:kind:cafe",
        "interpretation:2:fallback:purpose:outing",
    ]
    cafe, outing = outcome.suggestions
    assert cafe.basis_observation_ids == ("llm-test-1", "llm-test-2")
    assert outing.basis_observation_ids == ("llm-test-3",)
    assert cafe.result.plan is not None and outing.result.plan is not None
    assert any(
        gate.capability_id is CapabilityId.OPERATIONS_PARKING for gate in cafe.result.plan.gates
    )
    assert all(
        gate.capability_id is not CapabilityId.OPERATIONS_PARKING
        for gate in outing.result.plan.gates
    )


def test_failed_product_fallback_remains_visible_as_rejected() -> None:
    output = _materialize(
        "조용한 곳",
        (
            _proposal(
                IntentRole.REQUIRED_TARGET,
                SemanticIntent(concept_id="semantic.quiet"),
                "조용한 곳",
            ),
        ),
    )

    outcome = compile_intent_suggestions(
        output,
        spatial=_SPATIAL,
        limit_per_kind=2000,
    )

    assert [item.candidate_key for item in outcome.suggestions] == [
        "interpretation:1:fallback:purpose:dining",
        "interpretation:1:fallback:purpose:outing",
    ]
    assert [item.candidate_key for item in outcome.rejected] == [
        "interpretation:1",
        "interpretation:1:fallback:purpose:culture",
    ]
    failed = outcome.rejected[-1].result
    assert failed.status is PlannerStatus.NEEDS_CLARIFICATION
    assert failed.clarifications[0].code == "result_budget_exceeded"


@pytest.mark.parametrize(
    ("utterance", "proposals", "expected_status"),
    [
        (
            "조용하고 주차 필수",
            (
                _proposal(
                    IntentRole.REQUIRED_TARGET,
                    SemanticIntent(concept_id="semantic.quiet"),
                    "조용하고",
                ),
                _proposal(
                    IntentRole.REQUIRED_CONDITION,
                    BooleanCapabilityIntent(
                        capability_id=CapabilityId.OPERATIONS_PARKING,
                        value=True,
                    ),
                    "주차 필수",
                ),
            ),
            PlannerStatus.UNSUPPORTED,
        ),
        (
            "병원이나 갈까",
            (
                _proposal(
                    IntentRole.HYPOTHETICAL,
                    KindIntent(kind=PlaceKind.HOSPITAL),
                    "병원이나 갈까",
                ),
            ),
            PlannerStatus.NEEDS_CLARIFICATION,
        ),
        (
            "카페 말고 산책할 곳",
            (
                _proposal(
                    IntentRole.EXCLUDED,
                    KindIntent(kind=PlaceKind.CAFE),
                    "카페 말고",
                ),
                _proposal(
                    IntentRole.REQUIRED_TARGET,
                    PurposeIntent(purpose_id=PurposeId.OUTING),
                    "산책할 곳",
                ),
            ),
            PlannerStatus.UNSUPPORTED,
        ),
    ],
)
def test_product_fallback_never_weakens_missing_target_hard_or_healthcare_intents(
    utterance,
    proposals,
    expected_status,
) -> None:
    outcome = _compile(_materialize(utterance, proposals))

    assert outcome.status is expected_status
    assert not outcome.suggestions
    assert outcome.resolution is None
    assert outcome.rejected


def test_parking_preference_without_a_target_still_offers_broad_results() -> None:
    outcome = _compile(
        _materialize(
            "주차되면 좋겠어",
            (
                _proposal(
                    IntentRole.PREFERENCE,
                    BooleanCapabilityIntent(
                        capability_id=CapabilityId.OPERATIONS_PARKING,
                        value=True,
                    ),
                    "주차되면 좋겠어",
                ),
            ),
        )
    )

    assert outcome.status is PlannerStatus.READY
    assert len(outcome.suggestions) == 3
    assert all(item.result.plan is not None for item in outcome.suggestions)


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        (ProposalReason.INSUFFICIENT_TARGET, PlannerStatus.NEEDS_CLARIFICATION),
        (ProposalReason.UNSAFE_TO_GUESS, PlannerStatus.NEEDS_CLARIFICATION),
        (ProposalReason.UNSUPPORTED_LANGUAGE, PlannerStatus.UNSUPPORTED),
    ],
)
def test_abstention_reason_is_policy_input_not_an_executable_suggestion(reason, status) -> None:
    output = materialize_llm_output(
        "어디 갈까",
        LLMIntentOutput(
            disposition=ProposalDisposition.ABSTAINED,
            interpretations=(),
            reason=reason,
        ),
    )

    outcome = _compile(output)

    assert outcome.status is status
    assert not outcome.suggestions and not outcome.rejected
    assert outcome.issues[0].code == "intent_proposer_abstained"


class _StaticProposer:
    def __init__(self, output: LLMIntentOutput):
        self.output = output

    async def propose(self, utterance: str) -> LLMIntentOutput:
        return self.output


class _InvalidOutputProposer:
    async def propose(self, utterance: str) -> LLMIntentOutput:
        raise IntentProposerInvalidOutputError("provider details stay behind the boundary")


async def test_service_runs_proposer_grounding_and_suggestion_policy_end_to_end() -> None:
    raw = LLMIntentOutput(
        disposition=ProposalDisposition.PROPOSED,
        interpretations=(
            IntentInterpretation(
                proposals=(
                    _proposal(
                        IntentRole.REQUIRED_TARGET,
                        PurposeIntent(purpose_id=PurposeId.DINING),
                        "식사할 곳",
                    ),
                )
            ),
        ),
        reason=None,
    )
    service = PlaceIntentSuggestionService(
        _StaticProposer(raw),
        observation_id_factory=lambda: "server-observation",
    )

    outcome = await service.suggest(
        "식사할 곳",
        spatial=_SPATIAL,
        limit_per_kind=20,
    )

    assert outcome.status is PlannerStatus.READY
    assert outcome.suggestions[0].basis_observation_ids == ("server-observation",)


async def test_service_inspection_preserves_raw_and_grounded_layers() -> None:
    raw = LLMIntentOutput(
        disposition=ProposalDisposition.PROPOSED,
        interpretations=(
            IntentInterpretation(
                proposals=(
                    _proposal(
                        IntentRole.REQUIRED_TARGET,
                        PurposeIntent(purpose_id=PurposeId.DINING),
                        "식사할 곳",
                    ),
                )
            ),
        ),
        reason=None,
    )
    service = PlaceIntentSuggestionService(
        _StaticProposer(raw),
        observation_id_factory=lambda: "server-observation",
    )

    trace = await service.inspect(
        "식사할 곳",
        spatial=_SPATIAL,
        limit_per_kind=20,
    )

    assert trace.raw is raw
    assert trace.grounded is not None
    assert trace.normalized is not None
    assert trace.normalized.hypothesis_sets[0].hypotheses[0].hypothesis_key == (
        "interpretation:1:target"
    )
    assert trace.lenses is not None
    assert trace.lenses.target_lenses[0].display_label == "#식사·카페"
    assert trace.lenses.target_lenses[0].availability.value == "executable"
    assert trace.grounded.interpretations[0].observations[0].source.value == "llm_proposal"
    assert trace.outcome.status is PlannerStatus.READY


async def test_service_turns_ungrounded_model_evidence_into_safe_clarification() -> None:
    raw = LLMIntentOutput(
        disposition=ProposalDisposition.PROPOSED,
        interpretations=(
            IntentInterpretation(
                proposals=(
                    _proposal(
                        IntentRole.REQUIRED_TARGET,
                        KindIntent(kind=PlaceKind.CAFE),
                        "원문에 없는 카페 근거",
                    ),
                )
            ),
        ),
        reason=None,
    )
    service = PlaceIntentSuggestionService(_StaticProposer(raw))

    outcome = await service.suggest(
        "어디 갈까",
        spatial=_SPATIAL,
        limit_per_kind=20,
    )

    assert outcome.status is PlannerStatus.NEEDS_CLARIFICATION
    assert not outcome.suggestions
    assert outcome.issues[0].code == "intent_evidence_invalid"


async def test_service_turns_invalid_provider_output_into_typed_clarification() -> None:
    service = PlaceIntentSuggestionService(_InvalidOutputProposer())

    trace = await service.inspect(
        "강아지가 좋아하는거 있는곳",
        spatial=_SPATIAL,
        limit_per_kind=20,
    )

    assert trace.raw is None
    assert trace.grounded is None
    assert trace.normalized is None
    assert trace.lenses is None
    assert trace.outcome.status is PlannerStatus.NEEDS_CLARIFICATION
    assert trace.outcome.source_disposition is None
    assert not trace.outcome.suggestions
    assert trace.outcome.issues[0].code == "intent_proposer_invalid_output"
    assert "provider details" not in trace.outcome.issues[0].detail


def test_missing_source_disposition_requires_explicit_invalid_output_issue() -> None:
    with pytest.raises(
        ValueError,
        match="missing source disposition requires an empty invalid-output clarification",
    ):
        IntentSuggestionOutcome(
            status=PlannerStatus.NEEDS_CLARIFICATION,
            source_disposition=None,
            issues=(PlannerIssue(code="other_failure", detail="not the provider boundary"),),
        )

    with pytest.raises(
        ValueError,
        match="missing source disposition requires an empty invalid-output clarification",
    ):
        IntentSuggestionOutcome(
            status=PlannerStatus.UNSUPPORTED,
            source_disposition=None,
            issues=(
                PlannerIssue(
                    code="intent_proposer_invalid_output",
                    detail="right issue code cannot excuse a contradictory status",
                ),
            ),
        )
