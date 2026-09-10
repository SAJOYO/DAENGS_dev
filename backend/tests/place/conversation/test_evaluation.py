"""Verify the evaluator's oracle and observations, without a live provider or database."""

import json

import httpx
import pytest

from daengs_evals.place_conversation.checks import parking_mode
from daengs_evals.place_conversation.experiments import PolicyGemini, compile_replacement_branches
from daengs_evals.place_conversation.fixtures import FixtureSearcher, initial_state
from daengs_evals.place_conversation.provider import ObservedGemini
from daengs_evals.place_conversation.runner import DATA, read_cases, run_case
from daengs_place.place.conversation.contract import PrepareRequest, TurnPlan
from daengs_place.place.conversation.service import ConversationService, snapshot_hits
from daengs_place.place.tools.changes import apply_changes


def data(case_id):
    case = next(c for c in read_cases(DATA / "cases.v1.jsonl") if c["id"] == case_id)
    fixtures = json.loads((DATA / "fixtures.v1.json").read_text(encoding="utf-8"))
    return case, fixtures


async def test_fixture_preserves_null_and_filters_entire_pool_before_limit():
    case, fixtures = data("PC-E13")
    searcher = FixtureSearcher(case["setup"], fixtures)
    state = await initial_state(case["setup"], searcher)
    assert len(snapshot_hits(state.snapshot)) == 20
    plan = TurnPlan(
        goal="show",
        changes={
            "upsert_all": [
                {
                    "id": "parking",
                    "capability": "operations.parking",
                    "op": "eq",
                    "value": True,
                }
            ]
        },
    )
    filtered = await searcher(None, apply_changes(state.filters, plan.changes))
    assert [h.place.key.ref for h in filtered.groups[0].matched] == ["cafe-26"]

    case, fixtures = data("PC-N01")
    searcher = FixtureSearcher(case["setup"], fixtures)
    state = await initial_state(case["setup"], searcher)
    plan = TurnPlan(
        goal="show",
        changes={
            "upsert_all": [
                {
                    "id": "parking",
                    "capability": "operations.parking",
                    "op": "eq",
                    "value": False,
                }
            ]
        },
    )
    filtered = await searcher(None, apply_changes(state.filters, plan.changes))
    refs = [h.place.key.ref for g in filtered.groups for h in g.matched]
    assert refs == ["cafe-no", "restaurant-exclusive"]


async def test_or_fixture_keeps_branch_local_exclusive_and_parking():
    case, fixtures = data("PC-E05")
    searcher = FixtureSearcher(case["setup"], fixtures)
    state = await initial_state(case["setup"], searcher)
    plan = TurnPlan(
        goal="show",
        changes={
            "upsert_any": [
                {
                    "id": "a",
                    "all": [
                        {"id": "ak", "capability": "purpose.kind", "op": "in", "value": ["cafe"]},
                        {"id": "ap", "capability": "operations.parking", "op": "eq", "value": True},
                    ],
                },
                {
                    "id": "b",
                    "all": [
                        {
                            "id": "bk",
                            "capability": "purpose.kind",
                            "op": "in",
                            "value": ["restaurant"],
                        },
                        {
                            "id": "be",
                            "capability": "pet_access.exclusive",
                            "op": "eq",
                            "value": True,
                        },
                    ],
                },
            ]
        },
    )
    result = await searcher(None, apply_changes(state.filters, plan.changes))
    assert [h.place.key.ref for g in result.groups for h in g.matched] == [
        "cafe-yes",
        "restaurant-exclusive",
    ]


async def test_runner_records_raw_invalid_plan_without_leaking_oracle_or_key():
    case, fixtures = data("PC-N01")

    def response(request):
        payload = json.loads(request.content)
        assert "expect" not in payload["input"]
        assert "invariants" not in payload["input"]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "steps": [
                    {
                        "type": "function_call",
                        "name": "propose_facility_turn",
                        "arguments": {"goal": "show", "changes": {"radius_m": -1}},
                    }
                ],
            },
        )

    provider = ObservedGemini("secret-eval-key", "fake", transport=httpx.MockTransport(response))
    records = [record async for record in run_case(case, fixtures, provider, 1)]
    record = records[0]
    assert record["status"] == "fail"
    assert record["prepared"]["receipt"]["code"] == "invalid_plan"
    assert (
        record["provider_calls"][0]["response"]["steps"][0]["arguments"]["changes"]["radius_m"]
        == -1
    )
    assert "secret-eval-key" not in json.dumps(records)
    assert any(c["status"] == "review_required" for c in record["checks"])


async def test_provider_failure_marks_dependent_turns_not_run():
    case, fixtures = data("PC-E01")
    provider = ObservedGemini(
        "key", "fake", transport=httpx.MockTransport(lambda _: httpx.Response(503))
    )
    records = [record async for record in run_case(case, fixtures, provider, 1)]
    assert [r["status"] for r in records] == ["blocked", "not_run", "not_run"]


async def test_pending_clarification_baseline_drops_original_query():
    # A characterization for research: passing this is evidence of a gap, not desired behavior.
    case, fixtures = data("PC-E08")
    searcher = FixtureSearcher(case["setup"], fixtures)
    state = await initial_state(case["setup"], searcher)

    class Clarify:
        async def plan(self, request):
            return TurnPlan(goal="clarify", question="확인할 수 없는 조건을 빼고 찾을까요?")

    prepared = await ConversationService(Clarify(), searcher=searcher).prepare(
        None, PrepareRequest(mode="chat", query=case["steps"][0]["input"], previous=state)
    )
    assert prepared.state.history == ()
    assert parking_mode(prepared.state.filters) == "none"
    assert case["steps"][0]["input"] not in prepared.state.model_dump_json()


def test_replacement_compiler_reserves_existing_ids_and_rejects_incremental_ids():
    branches = [{"all": [{"capability": "operations.parking", "op": "eq", "value": True}]}]
    compiled = compile_replacement_branches(branches, ["expr-branch-0", "expr-0-atom-0"])
    assert compiled[0]["id"] != "expr-branch-0"
    assert compiled[0]["all"][0]["id"] != "expr-0-atom-0"
    with pytest.raises(ValueError):
        compile_replacement_branches([{"id": "incremental", **branches[0]}])


async def test_question_gate_blocks_changes_and_retains_original_until_consent():
    case, fixtures = data("PC-E08")
    requests = []

    def response(request):
        payload = json.loads(request.content)
        context = json.loads(payload["input"])
        requests.append(context)
        arguments = (
            {
                "goal": "show",
                "question": "확인 가능한 조건으로 찾아볼까요?",
                "changes": {"candidate_kinds": ["cafe"]},
            }
            if len(requests) == 1
            else {
                "goal": "show",
                "changes": {
                    "candidate_kinds": ["cafe"],
                    "upsert_all": [
                        {
                            "id": "parking",
                            "capability": "operations.parking",
                            "op": "eq",
                            "value": True,
                        }
                    ],
                },
            }
        )
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "steps": [
                    {
                        "type": "function_call",
                        "name": "propose_facility_turn",
                        "arguments": arguments,
                    }
                ],
            },
        )

    provider = PolicyGemini(
        "key",
        "fake",
        question_gate=True,
        pending_context=True,
        transport=httpx.MockTransport(response),
    )
    records = [r async for r in run_case(case, fixtures, provider, 1)]
    assert records[0]["prepared"]["receipt"]["execution"] == "not_run"
    assert records[0]["search_calls"] == 0
    assert requests[1]["pending_request"]["original_query"] == case["steps"][0]["input"]
    assert records[1]["prepared"]["receipt"]["execution"] == "searched"
    assert provider.pending is None
