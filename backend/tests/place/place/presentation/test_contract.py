import pytest
from pydantic import ValidationError

from daengs_place.place.contracts import PlaceRef
from daengs_place.place.presentation.contract import (
    DecisionRole,
    EvaluationState,
    LinkState,
    PlacePresentation,
    PresentationFact,
    PresentationFactId,
    PresentationItem,
    PresentationLinkReceipt,
    PresentationPlacement,
    PresentationPolicyReceipt,
    PresentationProvenance,
    SourceEvidenceSection,
    SourceRole,
    ValueOrigin,
)
from daengs_place.place.source_facts.states import EvidenceCertainty, FactState


def test_new_source_ids_fit_the_presentation_contract_without_source_branches() -> None:
    source = PlaceRef(source="city:realtime-crowding", ref="new-1")
    fact = PresentationFact(
        fact_id=PresentationFactId.OPERATIONS_PARKING,
        label="주차",
        value=False,
        display_text="주차 불가",
        source_state=FactState.KNOWN,
        evaluation_state=EvaluationState.NOT_EVALUATED,
        provenance=PresentationProvenance(
            source=source,
            source_role=SourceRole.PRIMARY,
            value_origin=ValueOrigin.OWN,
            evidence_certainty=EvidenceCertainty.SOURCE,
            source_field="parkingAvailable",
        ),
    )

    assert fact.provenance.source.source == "city:realtime-crowding"
    assert fact.value is False


def test_borrowed_value_requires_the_exact_supporting_link() -> None:
    primary = PlaceRef(source="kto", ref="K1")
    supporting = PlaceRef(source="kcisa", ref="C1")
    link = PresentationLinkReceipt(
        primary=primary,
        supporting=supporting,
        method="norm-name+150m",
        distance_m=21,
        state=LinkState.CANDIDATE,
    )
    provenance = PresentationProvenance(
        source=supporting,
        source_role=SourceRole.SUPPORTING,
        value_origin=ValueOrigin.BORROWED,
        link=link,
    )

    assert provenance.link is not None
    assert provenance.link.state is LinkState.CANDIDATE

    with pytest.raises(ValidationError, match="supporting source and link"):
        PresentationProvenance(
            source=supporting,
            source_role=SourceRole.SUPPORTING,
            value_origin=ValueOrigin.BORROWED,
        )


def test_source_state_evaluation_and_provenance_are_independent_axes() -> None:
    source = PlaceRef(source="kto", ref="K2")
    fact = PresentationFact(
        fact_id=PresentationFactId.PET_SIZE,
        label="크기 제한",
        display_text="KTO 상세 미수집",
        source_state=FactState.NOT_FETCHED,
        evaluation_state=EvaluationState.UNKNOWN,
        provenance=PresentationProvenance(
            source=source,
            source_role=SourceRole.PRIMARY,
            value_origin=ValueOrigin.OWN,
        ),
    )

    assert fact.source_state is FactState.NOT_FETCHED
    assert fact.evaluation_state is EvaluationState.UNKNOWN
    assert fact.provenance.value_origin is ValueOrigin.OWN

    with pytest.raises(ValidationError, match="conclusive evaluation requires"):
        PresentationFact(
            fact_id=PresentationFactId.PET_SIZE,
            label="크기 제한",
            display_text="KTO 상세 미수집",
            source_state=FactState.NOT_FETCHED,
            evaluation_state=EvaluationState.INCOMPATIBLE,
            provenance=fact.provenance,
        )


def test_known_and_non_known_values_cannot_contradict_their_state() -> None:
    provenance = PresentationProvenance(
        source=PlaceRef(source="kto", ref="K3"),
        source_role=SourceRole.PRIMARY,
        value_origin=ValueOrigin.OWN,
    )
    with pytest.raises(ValidationError, match="known presentation fact requires"):
        PresentationFact(
            fact_id=PresentationFactId.OPERATIONS_PARKING,
            label="주차",
            display_text="확인됨",
            source_state=FactState.KNOWN,
            provenance=provenance,
        )
    with pytest.raises(ValidationError, match="non-known presentation fact cannot"):
        PresentationFact(
            fact_id=PresentationFactId.OPERATIONS_PARKING,
            label="주차",
            value=False,
            display_text="미수집",
            source_state=FactState.NOT_FETCHED,
            provenance=provenance,
            decision_roles=(DecisionRole.DISPLAY,),
        )


def test_place_presentation_rejects_source_sections_that_disagree_with_fact_provenance() -> None:
    primary = PlaceRef(source="kto", ref="K4")
    fact = PresentationItem(
        fact_id=PresentationFactId.OPERATIONS_PARKING,
        label="주차",
        value=True,
        display_text="주차 가능",
        source_state=FactState.KNOWN,
        provenance=PresentationProvenance(
            source=primary,
            source_role=SourceRole.PRIMARY,
            value_origin=ValueOrigin.OWN,
        ),
        placement=PresentationPlacement.DETAIL,
    )
    receipt = PresentationPolicyReceipt(
        policy_id="place-presentation",
        policy_version="1",
        information_need_policy_id="place-information-needs",
        information_need_policy_version="1",
    )

    with pytest.raises(ValidationError, match="must match the presentation place key"):
        PlacePresentation(
            place_key=primary,
            title="테스트 장소",
            summary="테스트 요약",
            kind_id="travel",
            kind_label="여행지",
            distance_m=100,
            detail_items=(fact,),
            source_evidence=(
                SourceEvidenceSection(
                    source=PlaceRef(source="kcisa", ref="C4"),
                    source_role=SourceRole.PRIMARY,
                    adopted_fact_ids=(PresentationFactId.OPERATIONS_PARKING,),
                ),
            ),
            policy_receipt=receipt,
        )

    with pytest.raises(ValidationError, match="must cover every presented fact"):
        PlacePresentation(
            place_key=primary,
            title="테스트 장소",
            summary="테스트 요약",
            kind_id="travel",
            kind_label="여행지",
            distance_m=100,
            detail_items=(fact,),
            policy_receipt=receipt,
        )
