"""Ensure the experiment observes mixed events and detects failed rediscovery."""

import json

from daengs_evals.place_conversation.runner import DATA, read_cases, run_case
from daengs_place.place.conversation.intent import Interpretation


class Planner:
    def __init__(self, *intents):
        self.intents = iter(intents)
        self.calls, self.plans, self.drafts, self.requests = [], [], [], []

    async def plan(self, request):
        self.requests.append(request)
        intent = next(self.intents)
        self.plans.append(intent.model_dump(mode="json"))
        return intent


def case_and_fixtures(case_id):
    case = next(c for c in read_cases(DATA / "corrections.v1.jsonl") if c["id"] == case_id)
    fixtures = json.loads((DATA / "fixtures.policy.v1.json").read_text(encoding="utf-8"))
    return case, fixtures


async def test_manual_event_reaches_production_and_invalidates_pending_before_consent():
    case, fixtures = case_and_fixtures("PC-C03")
    planner = Planner(
        Interpretation(
            goal="show",
            changes={"kinds": {"operation": "set", "values": ["cafe"]}, "parking": "required_true"},
            unsupported=("quiet",),
        )
    )
    rows = [r async for r in run_case(case, fixtures, planner, 1)]
    assert len(rows) == 3
    assert all(r["status"] == "review_required" for r in rows)
    assert rows[0]["prepared"]["state"]["pending_proposal"] is not None
    assert rows[1]["event"]["action"] == "manual"
    assert rows[1]["plans"] == []
    assert rows[2]["before"] == rows[1]["prepared"]["state"]
    assert rows[2]["prepared"]["receipt"]["action"] == "clarify"
    assert rows[2]["prepared"]["state"]["filters"]["candidate_kinds"] == ["restaurant"]
    assert len(planner.requests) == 1


async def test_search_refresh_is_not_graded_as_new_results_when_refs_repeat():
    case, fixtures = case_and_fixtures("PC-U06")
    planner = Planner(Interpretation(goal="show", refresh=True))
    rows = [r async for r in run_case(case, fixtures, planner, 1)]
    row = rows[0]
    assert row["search_calls"] == 1
    assert row["prepared"]["receipt"]["execution"] == "searched"
    assert row["status"] == "fail"
    assert row["result_delta"] == {"added": [], "removed": []}
    assert (
        next(c for c in row["checks"] if c["criterion"] == "new_results.minimum")["status"]
        == "fail"
    )


async def test_explicit_correction_can_recover_to_a_place_outside_previous_twenty():
    case, fixtures = case_and_fixtures("PC-U09")
    planner = Planner(
        Interpretation(goal="show"),
        Interpretation(goal="show", changes={"parking": "required_true"}),
    )
    rows = [r async for r in run_case(case, fixtures, planner, 1)]
    assert rows[0]["status"] == "fail"
    assert rows[1]["status"] == "review_required"
    assert rows[1]["returned_refs"] == ["cafe-26"]
    assert rows[1]["result_delta"]["added"] == ["cafe-26"]
    assert len(rows[1]["result_delta"]["removed"]) == 20
