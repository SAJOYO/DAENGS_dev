from daengs_place.place.source_facts.contract import FactEvidence
from daengs_place.place.source_facts.states import (
    DetailAcquisitionState,
    FactState,
    acquisition_fact_state,
)


def test_boolean_value_and_fact_state_are_independent():
    evidence = FactEvidence(
        state=FactState.KNOWN,
        source_field="주차 가능여부",
        raw_value="N",
        parser_version="test/1",
    )
    assert evidence.state is FactState.KNOWN
    assert evidence.raw_value == "N"


def test_not_fetched_is_not_the_same_as_unknown():
    assert FactState.NOT_FETCHED != FactState.UNKNOWN


def test_detail_acquisition_state_maps_without_calling_failure_no_data():
    assert acquisition_fact_state(DetailAcquisitionState.NOT_FETCHED) is FactState.NOT_FETCHED
    assert acquisition_fact_state(DetailAcquisitionState.NO_DATA) is FactState.NOT_PROVIDED
    assert acquisition_fact_state(DetailAcquisitionState.FETCH_FAILED) is FactState.UNKNOWN
