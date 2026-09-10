from collections.abc import Iterator

import pytest

from daengs_place.place.intent.confirmation import confirm_search_lens
from daengs_place.place.intent.contract import (
    EvidenceQuote,
    IntentInterpretation,
    LLMIntentOutput,
    LLMIntentProposal,
    ProposalDisposition,
    materialize_llm_output,
)
from daengs_place.place.intent.hypotheses import build_search_hypotheses
from daengs_place.place.intent.lenses import LensAvailability, compile_search_lenses
from daengs_place.place.intent.suggestions import compile_intent_suggestions
from daengs_place.place.planning.contract import CapabilityId, GateOrigin, PlaceKind
from daengs_place.place.planning.intents import (
    BooleanCapabilityIntent,
    IntentRole,
    IntentSource,
    KindIntent,
    SemanticIntent,
)

_SPATIAL = {"lat": 37.5563, "lng": 126.9236, "radius_m": 3000}


def _ids(prefix: str = "confirmation-source") -> Iterator[str]:
    index = 0
    while True:
        index += 1
        yield f"{prefix}-{index}"


def _proposal(role: IntentRole, intent, quote: str) -> LLMIntentProposal:
    return LLMIntentProposal(
        role=role,
        intent=intent,
        evidence=EvidenceQuote(quote=quote, start=None, end=None),
    )


def _lens(utterance: str, *proposals: LLMIntentProposal):
    source_ids = _ids()
    grounded = materialize_llm_output(
        utterance,
        LLMIntentOutput(
            disposition=ProposalDisposition.PROPOSED,
            interpretations=(IntentInterpretation(proposals=proposals),),
            reason=None,
        ),
        id_factory=lambda: next(source_ids),
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
    ).target_lenses[0]


def _gate(result, capability_id: CapabilityId):
    assert result.plan is not None
    return next(gate for gate in result.plan.gates if gate.capability_id is capability_id)


def test_explicit_confirmation_replans_only_the_lens_target_as_user_confirmed() -> None:
    lens = _lens(
        "카페",
        _proposal(
            IntentRole.REQUIRED_TARGET,
            KindIntent(kind=PlaceKind.CAFE),
            "카페",
        ),
    )
    confirmation_ids = _ids("receipt")

    confirmed = confirm_search_lens(lens, id_factory=lambda: next(confirmation_ids))

    before = _gate(lens.candidate.result, CapabilityId.PURPOSE_KIND)
    after = _gate(confirmed.result, CapabilityId.PURPOSE_KIND)
    assert before.origin is GateOrigin.INFERRED
    assert before.locked is False
    assert after.value == before.value == (PlaceKind.CAFE,)
    assert after.origin is GateOrigin.USER_EXPLICIT
    assert after.locked is True
    assert after.relaxable is False
    assert [item.source for item in confirmed.confirmed_observations] == [
        IntentSource.USER_CONFIRMED
    ]
    assert confirmed.result.applied[0].observation_ids == (
        confirmed.confirmed_observations[0].observation_id,
    )
    assert "confirmation_context" not in lens.model_dump(mode="json")


def test_confirmation_preserves_the_source_lens_common_parking_preference() -> None:
    lens = _lens(
        "주차되면 좋은 카페",
        _proposal(
            IntentRole.REQUIRED_TARGET,
            KindIntent(kind=PlaceKind.CAFE),
            "카페",
        ),
        _proposal(
            IntentRole.PREFERENCE,
            BooleanCapabilityIntent(
                capability_id=CapabilityId.OPERATIONS_PARKING,
                value=True,
            ),
            "주차되면 좋은",
        ),
    )

    confirmed = confirm_search_lens(lens, id_factory=lambda: "parking")

    before = _gate(lens.candidate.result, CapabilityId.OPERATIONS_PARKING)
    after = _gate(confirmed.result, CapabilityId.OPERATIONS_PARKING)
    assert after.value == before.value is True
    assert after.origin is before.origin
    assert after.locked is False


def test_unresolved_lens_cannot_be_confirmed_around_its_required_selection() -> None:
    lens = _lens(
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
    assert lens.availability is LensAvailability.NEEDS_SELECTION

    with pytest.raises(ValueError, match="only an executable target lens"):
        confirm_search_lens(lens)
