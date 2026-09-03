import pytest
from pydantic import ValidationError

from daengs_place.place.planning.contract import CapabilityId, GateOrigin, PlaceKind
from daengs_place.place.planning.intents import (
    BooleanCapabilityIntent,
    IntentObservation,
    IntentProposal,
    IntentRole,
    IntentSource,
    KindIntent,
    PlannerIssue,
    PlannerRequest,
    PlannerResult,
    PlannerStatus,
    PurposeIntent,
    SemanticIntent,
    observe_intent,
)
from daengs_place.place.planning.planner import compile_intent_plan
from daengs_place.place.planning.purpose import PurposeId


def _request(*observations, limit_per_kind: int = 20) -> PlannerRequest:
    return PlannerRequest(
        spatial={"lat": 37.5, "lng": 127.0, "radius_m": 3000},
        observations=observations,
        limit_per_kind=limit_per_kind,
    )


def _kind(
    observation_id: str,
    *,
    source: IntentSource,
    role: IntentRole = IntentRole.REQUIRED_TARGET,
    kind: PlaceKind = PlaceKind.CAFE,
    evidence: str | None = None,
) -> IntentObservation:
    return observe_intent(
        IntentProposal(
            role=role,
            intent=KindIntent(kind=kind),
            evidence=evidence,
        ),
        source,
        observation_id=observation_id,
    )


def _purpose(
    observation_id: str,
    *,
    source: IntentSource,
    purpose_id: PurposeId,
) -> IntentObservation:
    return observe_intent(
        IntentProposal(
            role=IntentRole.REQUIRED_TARGET,
            intent=PurposeIntent(purpose_id=purpose_id),
        ),
        source,
        observation_id=observation_id,
    )


def _parking(
    observation_id: str,
    *,
    source: IntentSource,
    role: IntentRole,
) -> IntentObservation:
    return observe_intent(
        IntentProposal(
            role=role,
            intent=BooleanCapabilityIntent(
                capability_id=CapabilityId.OPERATIONS_PARKING,
                value=True,
            ),
        ),
        source,
        observation_id=observation_id,
    )


def _semantic(
    observation_id: str,
    *,
    source: IntentSource,
    concept_id: str,
    role: IntentRole = IntentRole.PREFERENCE,
    evidence: str | None = None,
) -> IntentObservation:
    return observe_intent(
        IntentProposal(
            role=role,
            intent=SemanticIntent(concept_id=concept_id),
            evidence=evidence,
        ),
        source,
        observation_id=observation_id,
    )


def test_extractor_proposal_schema_cannot_set_authority_or_lock_fields() -> None:
    proposal = IntentProposal(
        role=IntentRole.REQUIRED_TARGET,
        intent=KindIntent(kind=PlaceKind.CAFE),
    )

    assert set(proposal.model_json_schema()["properties"]) == {
        "role",
        "intent",
        "evidence",
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        IntentProposal.model_validate(
            {
                **proposal.model_dump(),
                "source": "ui_selection",
                "origin": "user_explicit",
                "locked": True,
            }
        )
    observed = observe_intent(
        proposal,
        IntentSource.LLM_PROPOSAL,
        observation_id="server-cafe",
    )
    assert observed.source is IntentSource.LLM_PROPOSAL
    assert observed.observation_id == "server-cafe"


@pytest.mark.parametrize(
    "source",
    [
        IntentSource.STRUCTURED_REQUEST,
        IntentSource.UI_SELECTION,
        IntentSource.USER_CONFIRMED,
        IntentSource.RULE_EXACT_COMMAND,
    ],
)
def test_only_trusted_literal_sources_can_lock_a_required_kind(
    source: IntentSource,
) -> None:
    result = compile_intent_plan(_request(_kind("target", source=source, evidence="카페만 찾아줘")))

    assert result.status is PlannerStatus.READY
    assert result.plan is not None
    purpose = result.plan.gates[0]
    assert purpose.value == (PlaceKind.CAFE,)
    assert purpose.origin is GateOrigin.USER_EXPLICIT
    assert purpose.locked is True
    assert purpose.relaxable is False
    assert "카페만 찾아줘" not in result.plan.model_dump_json()
    assert result.applied[0].observation_ids == ("target",)


@pytest.mark.parametrize(
    "source",
    [IntentSource.RULE_INFERENCE, IntentSource.LLM_PROPOSAL],
)
def test_rule_and_llm_proposals_cannot_claim_an_explicit_lock(
    source: IntentSource,
) -> None:
    result = compile_intent_plan(_request(_kind("proposal", source=source)))

    assert result.plan is not None
    purpose = result.plan.gates[0]
    assert purpose.origin is GateOrigin.INFERRED
    assert purpose.locked is False
    assert purpose.relaxable is True


def test_explicit_target_cannot_be_widened_by_an_llm_inference() -> None:
    result = compile_intent_plan(
        _request(
            _kind("explicit-cafe", source=IntentSource.UI_SELECTION),
            _purpose(
                "llm-dining",
                source=IntentSource.LLM_PROPOSAL,
                purpose_id=PurposeId.DINING,
            ),
        )
    )

    assert result.plan is not None
    assert result.plan.gates[0].value == (PlaceKind.CAFE,)
    assert result.not_applied[0].model_dump() == {
        "observation_ids": ("llm-dining",),
        "code": "shadowed_by_explicit_target",
        "detail": "an inferred target cannot widen an explicit user target",
    }


@pytest.mark.parametrize(
    "role",
    [IntentRole.ANALOGY, IntentRole.HYPOTHETICAL, IntentRole.RELATIONAL],
)
def test_non_literal_kind_mentions_never_become_a_gate(role: IntentRole) -> None:
    result = compile_intent_plan(
        _request(
            _kind(
                "figurative-cafe",
                source=IntentSource.RULE_INFERENCE,
                role=role,
                evidence="카페 같은 분위기",
            )
        )
    )

    assert result.status is PlannerStatus.NEEDS_CLARIFICATION
    assert result.plan is None
    assert result.unsupported[0].code == "non_literal_target"
    assert result.clarifications[0].code == "place_target_required"


def test_negated_kind_is_recorded_without_becoming_a_positive_target() -> None:
    result = compile_intent_plan(
        _request(
            _kind(
                "not-hospital",
                source=IntentSource.RULE_EXACT_COMMAND,
                role=IntentRole.NEGATED,
                kind=PlaceKind.HOSPITAL,
                evidence="병원 갈 정도는 아니야",
            )
        )
    )

    assert result.status is PlannerStatus.NEEDS_CLARIFICATION
    assert result.plan is None
    assert result.not_applied[0].code == "negated_mention"


def test_supported_target_can_run_while_unknown_semantics_remain_visible() -> None:
    result = compile_intent_plan(
        _request(
            _kind("cafe", source=IntentSource.UI_SELECTION),
            _semantic(
                "quiet",
                source=IntentSource.RULE_INFERENCE,
                concept_id="semantic.quiet",
                evidence="조용한",
            ),
        )
    )

    assert result.status is PlannerStatus.READY
    assert result.plan is not None
    assert result.plan.gates[0].value == (PlaceKind.CAFE,)
    assert result.unsupported[0].model_dump() == {
        "observation_ids": ("quiet",),
        "code": "unsupported_semantic_intent",
        "detail": "no executable capability for semantic.quiet",
    }


def test_parking_preference_uses_source_authority_but_hard_filter_is_rejected() -> None:
    preferred = compile_intent_plan(
        _request(
            _kind("cafe", source=IntentSource.UI_SELECTION),
            _parking(
                "rain-context",
                source=IntentSource.CONTEXT,
                role=IntentRole.PREFERENCE,
            ),
        )
    )
    required = compile_intent_plan(
        _request(
            _kind("cafe", source=IntentSource.UI_SELECTION),
            _parking(
                "parking-required",
                source=IntentSource.RULE_EXACT_COMMAND,
                role=IntentRole.REQUIRED_CONDITION,
            ),
        )
    )

    assert preferred.plan is not None
    assert preferred.plan.gates[1].origin is GateOrigin.CONTEXT
    assert preferred.applied[1].locked is False
    assert required.status is PlannerStatus.UNSUPPORTED
    assert required.plan is None
    assert required.unsupported[0].code == "unsupported_capability_strength"
    assert required.unsupported[0].blocking is True


def test_unsupported_exclusion_blocks_partial_execution() -> None:
    result = compile_intent_plan(
        _request(
            _kind("cafe", source=IntentSource.UI_SELECTION),
            _kind(
                "not-restaurant",
                source=IntentSource.USER_CONFIRMED,
                role=IntentRole.EXCLUDED,
                kind=PlaceKind.RESTAURANT,
            ),
        )
    )

    assert result.status is PlannerStatus.UNSUPPORTED
    assert result.plan is None
    assert result.unsupported[0].code == "unsupported_kind_exclusion"
    assert result.unsupported[0].blocking is True


def test_purpose_resolution_and_observation_order_are_deterministic() -> None:
    dining = _purpose(
        "dining",
        source=IntentSource.RULE_INFERENCE,
        purpose_id=PurposeId.DINING,
    )
    travel = _kind(
        "travel",
        source=IntentSource.RULE_INFERENCE,
        kind=PlaceKind.TRAVEL,
    )

    first = compile_intent_plan(_request(dining, travel))
    reversed_input = compile_intent_plan(_request(travel, dining))

    assert first.plan is not None and reversed_input.plan is not None
    assert (
        first.plan.gates[0].value
        == reversed_input.plan.gates[0].value
        == (
            PlaceKind.TRAVEL,
            PlaceKind.CAFE,
            PlaceKind.RESTAURANT,
        )
    )


def test_planner_refuses_silent_kind_or_result_budget_truncation() -> None:
    too_many = compile_intent_plan(
        _request(
            _purpose(
                "culture",
                source=IntentSource.USER_CONFIRMED,
                purpose_id=PurposeId.CULTURE,
            ),
            _purpose(
                "lodging",
                source=IntentSource.USER_CONFIRMED,
                purpose_id=PurposeId.LODGING,
            ),
        )
    )
    over_budget = compile_intent_plan(
        _request(
            _purpose(
                "culture",
                source=IntentSource.USER_CONFIRMED,
                purpose_id=PurposeId.CULTURE,
            ),
            limit_per_kind=2000,
        )
    )

    assert too_many.status is PlannerStatus.NEEDS_CLARIFICATION
    assert too_many.clarifications[0].code == "too_many_candidate_kinds"
    assert over_budget.status is PlannerStatus.NEEDS_CLARIFICATION
    assert over_budget.clarifications[0].code == "result_budget_exceeded"


def test_planner_request_requires_unique_server_assigned_observation_ids() -> None:
    with pytest.raises(ValidationError, match="observation ids must be unique"):
        _request(
            _kind("duplicate", source=IntentSource.UI_SELECTION),
            _kind(
                "duplicate",
                source=IntentSource.UI_SELECTION,
                kind=PlaceKind.RESTAURANT,
            ),
        )


def test_ready_result_contract_cannot_carry_a_blocking_unsupported_intent() -> None:
    valid = compile_intent_plan(_request(_kind("cafe", source=IntentSource.UI_SELECTION)))
    assert valid.plan is not None

    with pytest.raises(ValidationError, match="cannot ignore a blocking intent"):
        PlannerResult(
            status=PlannerStatus.READY,
            plan=valid.plan,
            applied=valid.applied,
            unsupported=(
                PlannerIssue(
                    observation_ids=("parking-required",),
                    code="unsupported_capability_strength",
                    detail="parking hard filter is unavailable",
                    blocking=True,
                ),
            ),
        )
