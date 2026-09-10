from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from daengs_place.place.intent.contract import (
    EvidenceQuote,
    IntentEvidenceError,
    IntentInterpretation,
    LLMIntentOutput,
    LLMIntentProposal,
    LLMSearchDirective,
    MaterializedIntentOutput,
    ProposalDisposition,
    ProposalReason,
    SearchModeId,
    materialize_llm_output,
)
from daengs_place.place.planning.contract import PlaceKind
from daengs_place.place.planning.intents import IntentRole, IntentSource, KindIntent


def _ids(*values: str) -> Iterator[str]:
    yield from values


def _kind_proposal(
    quote: str,
    *,
    role: IntentRole = IntentRole.REQUIRED_TARGET,
    start: int | None = None,
    end: int | None = None,
) -> LLMIntentProposal:
    return LLMIntentProposal(
        role=role,
        intent=KindIntent(kind=PlaceKind.CAFE),
        evidence=EvidenceQuote(quote=quote, start=start, end=end),
    )


def _output(*proposals: LLMIntentProposal) -> LLMIntentOutput:
    return LLMIntentOutput(
        disposition=ProposalDisposition.PROPOSED,
        interpretations=(IntentInterpretation(proposals=proposals),),
        reason=None,
    )


def test_llm_contract_has_no_audit_or_authority_fields() -> None:
    schema = LLMIntentOutput.model_json_schema()
    property_names: set[str] = set()

    def collect(value: object) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                property_names.update(properties)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(schema)
    assert {"observation_id", "source", "origin", "locked", "relaxable"}.isdisjoint(
        property_names
    )


def test_server_grounds_quote_and_assigns_id_and_source() -> None:
    output = _output(_kind_proposal("카페"))
    ids = _ids("server-1")

    materialized = materialize_llm_output("근처 카페 찾아줘", output, id_factory=lambda: next(ids))

    item = materialized.interpretations[0].items[0]
    assert item.evidence_span.model_dump() == {"start": 3, "end": 5, "text": "카페"}
    assert item.observation.observation_id == "server-1"
    assert item.observation.source is IntentSource.LLM_PROPOSAL
    assert item.observation.evidence == "카페"


def test_open_discovery_is_grounded_without_becoming_a_planner_observation() -> None:
    output = LLMIntentOutput(
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

    materialized = materialize_llm_output(
        "오늘 심심한데 네가 추천해봐",
        output,
        id_factory=lambda: pytest.fail("search directives must not receive observation ids"),
    )

    interpretation = materialized.interpretations[0]
    assert interpretation.observations == ()
    assert interpretation.search_directive.mode is SearchModeId.OPEN_DISCOVERY
    assert interpretation.search_directive.evidence_span.model_dump() == {
        "start": 8,
        "end": 15,
        "text": "네가 추천해봐",
    }


def test_search_mode_contract_requires_positive_delegation_evidence() -> None:
    with pytest.raises(ValidationError, match="requires delegation evidence"):
        LLMSearchDirective(mode=SearchModeId.OPEN_DISCOVERY)
    with pytest.raises(ValidationError, match="directed search requires"):
        IntentInterpretation(proposals=())
    with pytest.raises(ValidationError, match="cannot carry an explicit required target"):
        IntentInterpretation(
            search_directive=LLMSearchDirective(
                mode=SearchModeId.OPEN_DISCOVERY,
                evidence=EvidenceQuote(quote="추천해줘", start=None, end=None),
            ),
            proposals=(_kind_proposal("카페"),),
        )

    open_interpretation = IntentInterpretation(
        search_directive=LLMSearchDirective(
            mode=SearchModeId.OPEN_DISCOVERY,
            evidence=EvidenceQuote(quote="추천해줘", start=None, end=None),
        ),
        proposals=(),
    )
    with pytest.raises(ValidationError, match="positively proposed"):
        LLMIntentOutput(
            disposition=ProposalDisposition.AMBIGUOUS,
            interpretations=(
                open_interpretation,
                IntentInterpretation(proposals=(_kind_proposal("카페"),)),
            ),
            reason=ProposalReason.MULTIPLE_PLAUSIBLE_READINGS,
        )


def test_open_discovery_evidence_is_rejected_atomically_when_not_in_utterance() -> None:
    output = LLMIntentOutput(
        disposition=ProposalDisposition.PROPOSED,
        interpretations=(
            IntentInterpretation(
                search_directive=LLMSearchDirective(
                    mode=SearchModeId.OPEN_DISCOVERY,
                    evidence=EvidenceQuote(quote="네가 골라줘", start=None, end=None),
                ),
                proposals=(),
            ),
        ),
        reason=None,
    )

    with pytest.raises(IntentEvidenceError, match="not present"):
        materialize_llm_output("오늘 심심해", output)


def test_offsets_select_exact_original_substring() -> None:
    output = _output(_kind_proposal("카페", start=0, end=2))
    materialized = materialize_llm_output("카페만 찾아줘", output, id_factory=lambda: "server-1")
    assert materialized.interpretations[0].items[0].evidence_span.start == 0

    mismatched = _output(_kind_proposal("카페", start=1, end=3))
    with pytest.raises(IntentEvidenceError, match="do not select"):
        materialize_llm_output("카페만 찾아줘", mismatched)


def test_repeated_quote_requires_offsets_and_invalid_evidence_is_atomic() -> None:
    repeated = _output(_kind_proposal("카페"))
    with pytest.raises(IntentEvidenceError, match="requires explicit offsets"):
        materialize_llm_output("카페 말고 다른 카페", repeated)

    partly_invalid = _output(
        _kind_proposal("카페"),
        _kind_proposal("없는 근거", role=IntentRole.ANALOGY),
    )
    with pytest.raises(IntentEvidenceError, match="not present"):
        materialize_llm_output("카페 찾아줘", partly_invalid)


def test_alternative_interpretations_are_not_flattened() -> None:
    ambiguous = LLMIntentOutput(
        disposition=ProposalDisposition.AMBIGUOUS,
        interpretations=(
            IntentInterpretation(proposals=(_kind_proposal("카페"),)),
            IntentInterpretation(
                proposals=(_kind_proposal("카페", role=IntentRole.ANALOGY),)
            ),
        ),
        reason=ProposalReason.MULTIPLE_PLAUSIBLE_READINGS,
    )
    ids = _ids("candidate-a", "candidate-b")

    materialized = materialize_llm_output(
        "카페 같은 곳",
        ambiguous,
        id_factory=lambda: next(ids),
    )

    assert len(materialized.interpretations) == 2
    assert [
        interpretation.observations[0].role
        for interpretation in materialized.interpretations
    ] == [IntentRole.REQUIRED_TARGET, IntentRole.ANALOGY]


def test_disposition_contract_cannot_hide_malformed_candidate_shape() -> None:
    with pytest.raises(ValidationError, match="exactly one interpretation"):
        LLMIntentOutput(
            disposition=ProposalDisposition.PROPOSED,
            interpretations=(),
            reason=None,
        )
    with pytest.raises(ValidationError, match="cannot carry interpretations"):
        LLMIntentOutput(
            disposition=ProposalDisposition.ABSTAINED,
            interpretations=(
                IntentInterpretation(proposals=(_kind_proposal("카페"),)),
            ),
            reason=ProposalReason.UNSAFE_TO_GUESS,
        )


@pytest.mark.parametrize("output_type", [LLMIntentOutput, MaterializedIntentOutput])
def test_raw_and_materialized_outputs_share_disposition_invariants(
    output_type: type[LLMIntentOutput] | type[MaterializedIntentOutput],
) -> None:
    with pytest.raises(ValidationError, match="exactly one interpretation"):
        output_type(
            disposition=ProposalDisposition.PROPOSED,
            interpretations=(),
            reason=None,
        )
    with pytest.raises(ValidationError, match="abstained output requires a reason"):
        output_type(
            disposition=ProposalDisposition.ABSTAINED,
            interpretations=(),
            reason=None,
        )
