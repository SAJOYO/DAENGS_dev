"""Deterministic criteria only. Semantic answers always require a separate review."""

import json
from itertools import product

from daengs_place.place.conversation.service import fingerprint, snapshot_hits
from daengs_place.place.filters.evaluation import evaluate

from .fixtures import PARKING


def atoms(state):
    return (*state.hard.all, *(a for b in state.hard.any for a in b.all))


def parking_mode(state):
    hard = [a for a in atoms(state) if a.capability == PARKING]
    prefs = [p for p in state.preferences if p.capability == PARKING]
    if not hard:
        return "preferred_true" if prefs else "none"
    # A parking constraint nested in an OR is not globally required.
    if state.hard.any:
        return "branched"
    values = {a.value for a in hard}
    return (
        "required_true" if values == {True} else "required_false" if values == {False} else "mixed"
    )


def assess(case, step, before, prepared, answer):
    after, receipt = prepared.state, prepared.receipt
    expected = step["expect"]
    checks = []

    def check(name, actual, wanted):
        checks.append(
            {
                "criterion": name,
                "status": "pass" if actual == wanted else "fail",
                "actual": actual,
                "expected": wanted,
            }
        )

    for key in ("goal", "execution", "code", "returned_count", "filters_changed"):
        if key in expected:
            check(key, getattr(receipt, key), expected[key])
    for key in ("spatial", "dogs", "name_query", "unknown_policy", "result_policy"):
        if key in expected:
            check(key, getattr(after.filters, key), expected[key])
            continue
        check("preserve." + key, getattr(after.filters, key) == getattr(before.filters, key), True)
    if "candidate_kinds" in expected:
        check(
            "candidate_kinds",
            sorted(after.filters.candidate_kinds),
            sorted(expected["candidate_kinds"]),
        )
    elif case["id"] not in {"PC-E08", "PC-E12"}:
        check(
            "preserve.candidate_kinds",
            set(after.filters.candidate_kinds) == set(before.filters.candidate_kinds),
            True,
        )
    if expected.get("filters") == "unchanged":
        check("filters.unchanged", fingerprint(after.filters), fingerprint(before.filters))
    if "parking" in expected:
        check("parking", parking_mode(after.filters), expected["parking"])
    if expected.get("hard_parking") == "absent":
        check(
            "hard_parking.absent", any(a.capability == PARKING for a in atoms(after.filters)), False
        )
    if expected.get("parking_preferences") == "absent":
        check(
            "parking_preferences.absent",
            any(p.capability == PARKING for p in after.filters.preferences),
            False,
        )
    hits = snapshot_hits(after.snapshot)
    refs = [h.place.key.ref for h in hits]
    if "expected_refs" in expected:
        check("expected_refs", sorted(refs), sorted(expected["expected_refs"]))
        check("result_matches_filters", receipt.result_matches_filters, True)
    if "eligible_parking_values" in expected:
        # Test semantics, even when edit_only leaves the previous list visible.
        actual = [
            value
            for value in (True, False, None)
            if evaluate(after.filters, after.filters.candidate_kinds[0], value, None) is True
        ]
        check(
            "eligible_parking_values", set(actual) == set(expected["eligible_parking_values"]), True
        )
    if case["id"] == "PC-E05":
        mismatch = []
        for kind, parking, exclusive in product(
            ("cafe", "restaurant"), (True, False, None), (True, False, None)
        ):
            wanted = (kind == "cafe" and parking is True) or (
                kind == "restaurant" and exclusive is True
            )
            actual = evaluate(after.filters, kind, parking, exclusive) is True
            if actual != wanted:
                mismatch.append([kind, parking, exclusive])
        check("OR.truth_table", mismatch, [])
    if case["id"] == "PC-E10":
        check(
            "no_exclusive_substitution",
            any(a.capability == "pet_access.exclusive" for a in atoms(after.filters)),
            False,
        )
    selected = expected.get("selected")
    if selected == "현재 표시 후보 중 하나":
        check("selected.visible", receipt.selected in before.snapshot.display_order, True)
    elif selected:
        check("selected", receipt.selected.ref if receipt.selected else None, selected)
    # IDs can differ. Repeating a semantic condition in the same conjunction cannot.
    for index, group in enumerate(
        (
            after.filters.hard.all,
            *(b.all for b in after.filters.hard.any),
            after.filters.preferences,
        )
    ):
        semantic = [
            json.dumps(a.model_dump(mode="json", exclude={"id"}), sort_keys=True) for a in group
        ]
        check(f"duplicate_semantics.{index}", len(semantic), len(set(semantic)))
    check("answer.revision", answer.revision >= 1, True)
    checks.append(
        {
            "criterion": "answer_faithfulness_and_task_completion",
            "status": "review_required",
            "reason": "Read actual question, plan, committed facts and served answer; no keyword grading.",
        }
    )
    return checks
