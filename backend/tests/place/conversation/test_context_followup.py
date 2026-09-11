"""Verify the narrow follow-up manipulates the advertised factor only."""

import copy
import json

from daengs_evals.place_conversation.context_followup import arrange


def test_query_last_reorders_without_changing_any_values_or_prompt():
    payload = {
        "system_instruction": "fixed",
        "tools": [{"name": "fixed"}],
        "input": json.dumps({"query": "current", "history": [{"query": "old"}]}),
    }
    clues = {"screen": {"places": []}, "recent_interactions": [{"user_query": "old"}]}
    result = arrange(payload, clues, "query-last")
    actual = json.loads(result["input"])
    assert list(actual)[-1] == "query"
    assert actual == {**json.loads(payload["input"]), "context": clues}
    assert result["tools"] == payload["tools"]
    assert result["system_instruction"] == payload["system_instruction"]


def test_only_duplicate_query_is_removed_and_original_clues_are_immutable():
    payload = {"input": json.dumps({"query": "current", "history": [{"query": "old"}]})}
    clues = {
        "screen": {"places": [{"name": "A"}]},
        "recent_interactions": [
            {
                "user_query": "old",
                "mode": "manual",
                "assistant_text": "actual answer",
                "filters_before": {"candidate_kinds": ["cafe"]},
                "filters_after": {"candidate_kinds": ["cafe", "restaurant"]},
            }
        ],
    }
    before = copy.deepcopy(clues)
    actual = json.loads(arrange(payload, clues, "no-duplicate-past-query")["input"])
    assert actual["history"] == [{"query": "old"}]
    assert next(iter(actual)) == "query"
    expected = copy.deepcopy(before)
    expected["recent_interactions"][0].pop("user_query")
    assert actual["context"] == expected
    assert clues == before
