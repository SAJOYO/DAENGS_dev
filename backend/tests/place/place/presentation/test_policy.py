import pytest

from daengs_place.place.contracts import PlaceRef
from daengs_place.place.presentation.contract import (
    DecisionRole,
    LinkState,
    PresentationFact,
    PresentationFactId,
    PresentationLinkReceipt,
    PresentationPlacement,
    PresentationProvenance,
    SourceRole,
    ValueOrigin,
)
from daengs_place.place.presentation.needs import InformationNeedId
from daengs_place.place.presentation.policy import (
    PRESENTATION_POLICY_ID,
    PRESENTATION_POLICY_VERSION,
    arrange_presentation,
)
from daengs_place.place.source_facts.states import FactState


def _own_fact(
    fact_id: PresentationFactId,
    *,
    value=True,
    state: FactState = FactState.KNOWN,
    display_text: str = "확인됨",
) -> PresentationFact:
    return PresentationFact(
        fact_id=fact_id,
        label=fact_id.value,
        value=value if state is FactState.KNOWN else None,
        display_text=display_text,
        source_state=state,
        provenance=PresentationProvenance(
            source=PlaceRef(source="future:official-api", ref=fact_id.value),
            source_role=SourceRole.PRIMARY,
            value_origin=ValueOrigin.OWN,
        ),
    )


def test_information_need_promotes_one_summary_item_without_core_duplication() -> None:
    distance = _own_fact(PresentationFactId.PLACE_DISTANCE, value=850)
    result = arrange_presentation(
        (distance, _own_fact(PresentationFactId.PLACE_KIND, value="pet_shop")),
        (InformationNeedId.COST_TRAVEL_DISTANCE,),
    )

    assert [item.fact_id for item in result.promoted_items] == [PresentationFactId.PLACE_DISTANCE]
    assert [item.fact_id for item in result.core_items] == [PresentationFactId.PLACE_KIND]
    assert result.promoted_items[0].placement is PresentationPlacement.PROMOTED
    assert result.promoted_items[0].promoted_by == ("cost.travel_distance",)


def test_missing_requested_fact_is_promoted_as_unknown_and_gets_fallback_notice() -> None:
    products = _own_fact(
        PresentationFactId.PET_PRODUCTS_PURCHASABLE,
        state=FactState.NOT_FETCHED,
        display_text="KTO 상품 상세 미수집",
    )
    result = arrange_presentation(
        (products,),
        (InformationNeedId.PRODUCTS_PURCHASABLE,),
    )

    assert result.promoted_items[0].source_state is FactState.NOT_FETCHED
    assert {notice.code for notice in result.notices} == {"products.purchasable.unverified"}


def test_known_empty_product_list_is_not_positive_purchase_evidence() -> None:
    result = arrange_presentation(
        (_own_fact(PresentationFactId.PET_PRODUCTS_PURCHASABLE, value=[]),),
        (InformationNeedId.PRODUCTS_PURCHASABLE,),
    )

    assert "products.purchasable.unverified" in {notice.code for notice in result.notices}


def test_information_need_without_a_canonical_fact_emits_an_honest_notice() -> None:
    result = arrange_presentation((), (InformationNeedId.AMBIENCE_QUIET,))

    assert not result.promoted_items
    assert result.notices[0].code == "ambience.quiet.unavailable"
    assert "근거 데이터" in result.notices[0].message


def test_candidate_link_fact_used_for_ranking_is_visible_in_notices() -> None:
    primary = PlaceRef(source="kto", ref="K1")
    supporting = PlaceRef(source="another-public-api", ref="S1")
    link = PresentationLinkReceipt(
        primary=primary,
        supporting=supporting,
        method="norm-name+150m",
        distance_m=10,
        state=LinkState.CANDIDATE,
    )
    parking = PresentationFact(
        fact_id=PresentationFactId.OPERATIONS_PARKING,
        label="주차",
        value=True,
        display_text="주차 가능",
        source_state=FactState.KNOWN,
        provenance=PresentationProvenance(
            source=supporting,
            source_role=SourceRole.SUPPORTING,
            value_origin=ValueOrigin.BORROWED,
            link=link,
        ),
        decision_roles=(DecisionRole.DISPLAY, DecisionRole.RANKING),
    )

    result = arrange_presentation((parking,))

    notice = next(item for item in result.notices if item.code.endswith("candidate_link_decision"))
    assert "정렬" in notice.message


def test_hours_do_not_claim_that_open_now_was_evaluated() -> None:
    result = arrange_presentation(
        (
            _own_fact(PresentationFactId.OPERATIONS_HOURS, value="09:00~18:00"),
            _own_fact(PresentationFactId.OPERATIONS_CLOSED_DAYS, value="매주 월요일"),
        ),
        (InformationNeedId.OPERATIONS_OPEN_NOW,),
    )

    assert {item.fact_id for item in result.promoted_items} == {
        PresentationFactId.OPERATIONS_HOURS,
        PresentationFactId.OPERATIONS_CLOSED_DAYS,
    }
    notice = next(item for item in result.notices if item.code == "operations.open_now.unverified")
    assert "확정할 수 없습니다" in notice.message


def test_generic_restrictions_do_not_claim_that_size_is_known() -> None:
    result = arrange_presentation(
        (_own_fact(PresentationFactId.PET_RESTRICTIONS, value=[]),),
        (InformationNeedId.PET_SIZE,),
    )

    assert "pet.size.unknown" in {notice.code for notice in result.notices}


def test_need_fallback_replaces_a_duplicate_generic_unknown_notice() -> None:
    result = arrange_presentation(
        (
            _own_fact(
                PresentationFactId.PET_SIZE,
                state=FactState.NOT_FETCHED,
                display_text="KTO 크기 상세 미수집",
            ),
        ),
        (InformationNeedId.PET_SIZE,),
    )

    assert {notice.code for notice in result.notices} == {"pet.size.unknown"}
    assert not any(
        rule == "notice:pet_access.size.not_fetched"
        for rule in result.policy_receipt.applied_rule_ids
    )


def test_policy_rejects_multiple_effective_values_for_the_same_fact() -> None:
    facts = (
        _own_fact(PresentationFactId.OPERATIONS_HOURS, value="09:00~18:00"),
        _own_fact(PresentationFactId.OPERATIONS_HOURS, value="10:00~19:00"),
    )

    with pytest.raises(ValueError, match="one effective value"):
        arrange_presentation(facts)


def test_policy_receipts_are_stable_and_versioned() -> None:
    result = arrange_presentation(
        (_own_fact(PresentationFactId.OPERATIONS_PARKING),),
        (InformationNeedId.OPERATIONS_PARKING,),
    )

    assert result.policy_receipt.policy_id == PRESENTATION_POLICY_ID
    assert result.policy_receipt.policy_version == PRESENTATION_POLICY_VERSION
    assert result.policy_receipt.information_need_policy_id == "place-information-needs"
    assert result.policy_receipt.information_need_policy_version == "1"
    assert result.policy_receipt.information_need_ids == ("operations.parking",)
