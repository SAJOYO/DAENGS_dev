from copy import deepcopy
from types import SimpleNamespace

import pytest

from daengs_evals.facility_tools.evaluate import SCENARIOS, verify
from daengs_place.place.commands.view import tool_result, ui_view


async def evidence(workspace, name, args):
    before = ui_view(workspace.state)
    result = await workspace.execute("evaluation", name, args, workspace.state.revision)
    turn = SimpleNamespace(
        status="ready",
        executions=[
            {
                "name": name,
                "arguments": args,
                "result": tool_result(result),
            }
        ],
    )
    return before, ui_view(workspace.state), turn


async def detail_evidence(workspace):
    before = ui_view(workspace.state)
    return await evidence(
        workspace,
        "get_place_details",
        {
            "place_refs": [before["cards"][1]["ref"]],
            "attributes": ["parking"],
        },
    )


async def test_valid_detail_and_failed_attempt_followed_by_recovery_pass(workspace):
    before, after, turn = await detail_evidence(workspace)
    expected = SCENARIOS[0][3][1]
    assert verify(expected, before, after, turn) == []
    failed = deepcopy(turn.executions[0])
    failed["result"] = {"status": "failed", "code": "invalid_arguments"}
    turn.executions.insert(0, failed)
    assert verify(expected, before, after, turn) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_target",
        "wrong_attribute",
        "failed",
        "wrong_fact",
        "missing_fact",
        "wrong_fact_target",
        "extra_action",
    ],
)
async def test_incorrect_detail_does_not_pass_with_unchanged_state(workspace, mutation):
    before, after, turn = await detail_evidence(workspace)
    execution = turn.executions[0]
    if mutation == "wrong_target":
        execution["arguments"]["place_refs"] = [before["cards"][0]["ref"]]
    elif mutation == "wrong_attribute":
        execution["arguments"]["attributes"] = ["hours"]
    elif mutation == "failed":
        execution["result"] = {"status": "failed", "code": "invalid_arguments"}
    elif mutation == "wrong_fact":
        execution["result"]["places"][0]["facts"]["parking"]["value"] = True
    elif mutation == "missing_fact":
        execution["result"]["places"][0]["facts"] = {}
    elif mutation == "wrong_fact_target":
        execution["result"]["places"][0]["ref"] = before["cards"][0]["ref"]
    else:
        extra = deepcopy(execution)
        extra["name"] = "select_place"
        turn.executions.append(extra)
    assert before == after
    assert verify(SCENARIOS[0][3][1], before, after, turn)


@pytest.mark.parametrize("mutation", [None, "category", "parking", "unavailable", "applied"])
async def test_proposal_content_and_non_application_are_checked(workspace, mutation):
    before, after, turn = await evidence(
        workspace,
        "search_places",
        {
            "category": {"operation": "set", "values": ["cafe"]},
            "parking": "required",
            "unavailable": ["조용함"],
        },
    )
    proposal = after["pending_proposal"]
    if mutation == "category":
        proposal["filters"]["kinds"] = ["restaurant"]
    elif mutation == "parking":
        proposal["filters"]["required"] = []
    elif mutation == "unavailable":
        proposal["unavailable"] = []
    elif mutation == "applied":
        after["filters"] = deepcopy(proposal["filters"])
    assert bool(verify(SCENARIOS[1][0][1], before, after, turn)) == (mutation is not None)


async def test_exhaustion_requires_preserved_view_and_result_evidence(workspace):
    before, after, turn = await evidence(workspace, "next_places", {})
    expected = {
        **SCENARIOS[0][6][1],
        "cards": "ABCDEF",
        "result": {"code": "no_more_candidates", "visible_count": 6, "new_count": 0},
    }
    assert verify(expected, before, after, turn) == []
    turn.executions[0]["result"]["visible_count"] = 0
    assert verify(expected, before, after, turn)
