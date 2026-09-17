"""Regressions from actual OR-search/category-change and recommendation failures."""

from itertools import product

import pytest

from daengs_place.place.conversation.compiler import compile_changes
from daengs_place.place.conversation.intent import ScopedInterpretation, SemanticChanges
from daengs_place.place.conversation.search_compilation import compile_saved, from_search
from daengs_place.place.filters.contract import FilterState
from daengs_place.place.filters.evaluation import evaluate
from tests.place.conversation.test_policy import chat
from tests.place.conversation.test_scope import initial
from tests.place.support.conversation import Planner, manual


def branched(**changes):
    state = FilterState(
        candidate_kinds=("cafe", "restaurant"),
        spatial={"lat": 37.5, "lng": 127, "radius_m": 3000},
    )
    return compile_changes(
        state,
        SemanticChanges(
            alternatives=(
                {"kinds": ["cafe"], "parking": True},
                {"kinds": ["restaurant"], "exclusive": True},
            ),
            **changes,
        ),
    )


@pytest.mark.parametrize("query", ["재밌는데", "강아지랑 놀 수 있는 곳"])
async def test_or_then_activity_search_drops_only_old_category_local_conditions(query):
    plan = ScopedInterpretation(
        kind="facility_action",
        goal="show",
        changes={"kinds": {"operation": "set", "values": ["leisure"]}},
    )
    service, searcher, _ = await initial(Planner(plan))
    before = (await service.prepare(None, manual(kinds=["cafe", "restaurant"]))).state
    before = before.model_copy(update={"filters": branched(exclusive="required_true")})
    count = len(searcher.calls)
    after = await chat(service, before, query)
    assert after.receipt.execution == "searched"
    assert len(searcher.calls) == count + 1
    assert after.state.filters.candidate_kinds == ("leisure",)
    assert after.state.filters.hard.any == ()
    assert evaluate(after.state.filters, "leisure", False, True) is True
    assert evaluate(after.state.filters, "leisure", True, False) is False
    assert after.state.filters.spatial == before.filters.spatial


@pytest.mark.parametrize(
    "operation,values",
    [
        ("set", ["cafe"]),
        ("remove", ["restaurant"]),
        ("add", ["hotel"]),
        ("set", ["restaurant", "hotel"]),
    ],
)
def test_retained_branches_keep_truth_table_and_new_categories_inherit_no_local_facts(
    operation, values
):
    before = branched()
    raw = before.model_dump(mode="json")
    after = compile_changes(
        before, SemanticChanges(kinds={"operation": operation, "values": values})
    )
    for kind in after.candidate_kinds:
        for parking, exclusive in product((None, False, True), repeat=2):
            expected = True if kind == "hotel" else evaluate(before, kind, parking, exclusive)
            assert evaluate(after, kind, parking, exclusive) is expected
    assert before.model_dump(mode="json") == raw
    assert compile_changes(after, SemanticChanges()) == after


def test_global_parking_and_unscoped_disjunction_survive_category_replacement():
    before = branched(parking="preferred_true")
    after = compile_changes(
        before, SemanticChanges(kinds={"operation": "set", "values": ["hotel"]})
    )
    assert after.preferences[0].scope_kinds == ("hotel",)
    assert after.preferences[0].capability == "operations.parking"
    global_or = compile_changes(
        before,
        SemanticChanges(
            parking="clear",
            kinds={"operation": "set", "values": ["cafe", "restaurant"]},
            alternatives=({"parking": True}, {"exclusive": True}),
        ),
    )
    after = compile_changes(
        global_or, SemanticChanges(kinds={"operation": "set", "values": ["hotel"]})
    )
    assert evaluate(after, "hotel", False, False) is False
    assert evaluate(after, "hotel", True, False) is True
    assert evaluate(after, "hotel", None, False) is None
    global_and = compile_changes(global_or, SemanticChanges(parking="required_true"))
    after = compile_changes(
        global_and, SemanticChanges(kinds={"operation": "set", "values": ["hotel"]})
    )
    assert evaluate(after, "hotel", False, True) is False
    assert evaluate(after, "hotel", True, False) is True


def test_saved_and_ordinary_category_replacement_use_identical_algebra():
    before = branched()
    changes = SemanticChanges(kinds={"operation": "set", "values": ["restaurant", "hotel"]})
    ordinary = compile_changes(before, changes)
    saved = compile_saved(from_search(before), changes, "keep")
    assert saved.hard == ordinary.hard
    assert saved.kinds == list(ordinary.candidate_kinds)


@pytest.mark.parametrize(
    "changes",
    [
        {"kinds": {"operation": "remove", "values": ["cafe", "restaurant"]}},
        {"alternatives": [{"kinds": ["hotel"], "parking": True}]},
    ],
)
async def test_invalid_execution_bounds_preserve_original_conditions_and_do_not_search(
    changes, caplog
):
    service, searcher, _ = await initial(
        Planner(
            ScopedInterpretation(
                kind="facility_action",
                goal="show",
                changes=changes,
            )
        )
    )
    before = (await service.prepare(None, manual(kinds=["cafe", "restaurant"]))).state
    count = len(searcher.calls)
    after = await chat(service, before, "private user message")
    assert after.receipt.code == "invalid_plan"
    assert after.state.model_dump(exclude={"revision"}) == before.model_dump(exclude={"revision"})
    assert len(searcher.calls) == count
    assert "facility_decision_failed" in caplog.text
    assert "private user message" not in caplog.text
