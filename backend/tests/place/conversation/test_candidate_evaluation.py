"""Evaluate equivalent AND/OR encodings instead of counting one syntactic atom."""

from daengs_evals.place_conversation.candidates import parking_matches
from daengs_place.place.conversation.compiler import compile_changes
from daengs_place.place.conversation.intent import Interpretation
from daengs_place.place.conversation.static_tools import TURN_TOOL
from daengs_place.place.filters.contract import FilterState


def test_parking_or_branch_and_global_condition_have_the_same_evaluation():
    initial = FilterState(
        candidate_kinds=("cafe",), spatial={"lat": 37.5, "lng": 127, "radius_m": 3000}
    )
    for changes in (
        {"parking": "required_true"},
        {"alternatives": [{"kinds": ["cafe"], "parking": True}]},
    ):
        filters = compile_changes(initial, Interpretation(goal="show", changes=changes).changes)
        assert parking_matches(filters, True)
        assert not parking_matches(filters, False)
    assert parking_matches(initial, False)
    assert not parking_matches(initial, True)


def test_tool_requires_semantic_fields_but_not_verbatim_read_search_evidence():
    required = set(TURN_TOOL["parameters"]["required"])
    assert {"kind", "goal", "feedback", "changes"} <= required
    assert not {"request_quote", "search_scope_quote"} & required
