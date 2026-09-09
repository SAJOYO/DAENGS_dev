import json
from itertools import product
from pathlib import Path

import pytest
from pydantic import ValidationError

from daengs_place.ingest.kcisa import _flag
from daengs_place.place.filters.contract import Atom, FilterState, guard_filter_state
from daengs_place.place.filters.evaluation import all_true, any_true, evaluate

FIXTURE = json.loads((Path(__file__).parent / "examples.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURE["predicate_cases"], ids=lambda c: c["id"])
def test_approved_truth_examples(case):
    state = FilterState.model_validate(case["state"])
    groups = {"matched": [], "uncertain": [], "excluded": []}
    for row in FIXTURE["candidates"]:
        value = evaluate(state, row["kind"], row["parking"], row["exclusive"])
        groups["uncertain" if value is None else "matched" if value else "excluded"].append(
            row["id"]
        )
    for group, values in groups.items():
        assert sorted(values) == sorted(case["expected"][group])


@pytest.mark.parametrize("left,right", list(product((True, False, None), repeat=2)))
def test_three_value_truth_table(left, right):
    expected_and = {
        (True, True): True,
        (True, False): False,
        (True, None): None,
        (False, True): False,
        (False, False): False,
        (False, None): False,
        (None, True): None,
        (None, False): False,
        (None, None): None,
    }
    expected_or = {
        (True, True): True,
        (True, False): True,
        (True, None): True,
        (False, True): True,
        (False, False): False,
        (False, None): None,
        (None, True): True,
        (None, False): None,
        (None, None): None,
    }
    assert all_true((left, right)) is expected_and[left, right]
    assert any_true((left, right)) is expected_or[left, right]


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}, ["cafe"]])
def test_bool_never_coerces(value):
    with pytest.raises(ValidationError):
        Atom(id="a", capability="operations.parking", op="eq", value=value)


def state_payload():
    return {
        "candidate_kinds": ["cafe", "restaurant"],
        "spatial": {"lat": 37.4979, "lng": 130.9, "radius_m": 3000},
        "hard": {"all": [], "any": []},
    }


def parking(value, id="p"):
    return {"id": id, "capability": "operations.parking", "op": "eq", "value": value}


def test_conflicting_branch_cannot_be_silently_dropped():
    value = state_payload()
    value["hard"]["any"] = [
        {"id": "bad", "all": [parking(True, "p1"), parking(False, "p2")]},
        {"id": "good", "all": [parking(True, "p3")]},
    ]
    with pytest.raises(ValidationError, match="contradictory_filters"):
        FilterState.model_validate(value)


def test_global_and_branch_conflict_is_rejected():
    value = state_payload()
    value["hard"] = {"all": [parking(True)], "any": [{"id": "b", "all": [parking(False, "p2")]}]}
    with pytest.raises(ValidationError, match="contradictory_filters"):
        FilterState.model_validate(value)


@pytest.mark.parametrize(
    "change",
    [
        {"hard": {"any": [{"id": "b", "all": []}]}},
        {"candidate_kinds": []},
        {"candidate_kinds": ["cafe", "cafe"]},
        {"unknown_policy": "keep"},
        {"unknown_policy": "separate"},
        {"result_policy": {"limit_per_kind": True}},
        {"result_policy": {"limit_per_kind": 3000}},
        {"extra": True},
        {"hard": {"all": [{"id": "fee", "capability": "cost.pet_fee", "op": "eq", "value": 0}]}},
    ],
)
def test_invalid_requests_fail_before_execution(change):
    with pytest.raises(ValidationError):
        FilterState.model_validate(state_payload() | change)


def test_guard_does_not_trust_unvalidated_model_copy():
    state = FilterState.model_validate(state_payload())
    with pytest.raises(ValidationError):
        guard_filter_state(state.model_copy(update={"candidate_kinds": ()}))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Y", True),
        (" N ", False),
        ("현장 문의", None),
        ("정보없음", None),
        ("", None),
        (None, None),
    ],
)
def test_ingest_boolean_preserves_unknown(raw, expected):
    assert _flag(raw) is expected


def test_preference_cannot_contradict_hard_in_its_scope():
    value = state_payload()
    value["hard"]["all"] = [parking(False)]
    value["preferences"] = [parking(True, "prefer") | {"scope_kinds": ["cafe"]}]
    with pytest.raises(ValidationError, match="contradictory_preference"):
        FilterState.model_validate(value)
