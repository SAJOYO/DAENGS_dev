"""Verify the intervention changes context alone and cannot see future/oracle data."""

import copy
import json
from datetime import datetime

import httpx

from daengs_evals.place_conversation.context_ablation import (
    ContextGemini,
    anchors,
    context_clues,
    evaluate_target,
)
from daengs_evals.place_conversation.runner import DATA
from daengs_place.place.conversation.contract import ConversationState
from tests.place.support.conversation import scoped_wire


async def samples():
    spec = json.loads((DATA / "context-ablation.v1.json").read_text(encoding="utf-8"))
    source = DATA / "runs" / spec["source_run"]
    fixtures = json.loads((source / "fixtures.json").read_text(encoding="utf-8"))
    return await anchors(spec, source, fixtures), fixtures


def response(arguments):
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


async def test_paired_input_only_adds_context_but_cannot_authorize_ungrounded_undo():
    targets, fixtures = await samples()
    target = next(t for t in targets if t["id"] == "PC-X03")
    wires = []

    def capture(request):
        wire = json.loads(request.content)
        wires.append(wire)
        has_context = "context" in json.loads(wire["input"])
        return response(
            scoped_wire(
                {
                    "goal": "show",
                    "changes": {"kinds": {"operation": "remove", "values": ["restaurant"]}},
                }
                if has_context
                else {"goal": "show"},
                json.loads(wire["input"])["query"],
            )
        )

    provider = ContextGemini("synthetic-key", "mock", transport=httpx.MockTransport(capture))
    a = await evaluate_target(target, fixtures, provider, "current-input", 1)
    b = await evaluate_target(target, fixtures, provider, "context-input", 1)
    a2 = await evaluate_target(target, fixtures, provider, "current-input", 2)
    assert a["anchor_sha256"] == b["anchor_sha256"] == a2["anchor_sha256"]
    assert a["before"] == b["before"] == a2["before"]
    assert a["prepared"]["state"]["filters"]["candidate_kinds"] == ["cafe", "restaurant"]
    # This historical anchor has no committed intent history. Extra model clues alone
    # cannot authorize "undo what I just added" under the facility scope contract.
    assert b["plans"][0]["changes"]["kinds"]["values"] == ["restaurant"]
    assert b["prepared"]["receipt"]["code"] == "invalid_plan"
    assert b["prepared"]["state"]["filters"]["candidate_kinds"] == ["cafe", "restaurant"]
    assert a2["prepared"]["state"]["filters"]["candidate_kinds"] == ["cafe", "restaurant"]
    enriched = json.loads(wires[1]["input"])
    enriched.pop("context")
    assert enriched == json.loads(wires[0]["input"]) == json.loads(wires[2]["input"])
    assert {k: v for k, v in wires[0].items() if k != "input"} == {
        k: v for k, v in wires[1].items() if k != "input"
    }


async def test_context_contains_only_displayed_places_and_allowlisted_completed_events():
    targets, _ = await samples()
    more = next(t for t in targets if t["id"] == "PC-X09")
    encoded = json.dumps(more["clues"], ensure_ascii=False)
    assert len(more["clues"]["screen"]["places"]) == 20
    assert "cafe-26" not in encoded
    assert more["clues"]["screen"]["groups"][0]["more_matches_than_displayed"] is True
    target = next(t for t in targets if t["id"] == "PC-X03")
    state = ConversationState.model_validate(target["before"])
    row = {
        "event": {"action": "manual", "expect": "ORACLE"},
        "query": None,
        "before": target["before"],
        "prepared": {"state": target["before"]},
        "served_answer": {"text": "actual delivered answer", "review": "ORACLE"},
        "review": "ORACLE",
        "expect": "ORACLE",
        "next_query": "FUTURE",
    }
    clues = context_clues(state, [row], datetime.fromisoformat(target["clock"]))
    assert "ORACLE" not in json.dumps(clues)
    assert "FUTURE" not in json.dumps(clues)
    assert clues["recent_interactions"][0]["assistant_text"] == "actual delivered answer"
    closure = next(t for t in targets if t["id"] == "PC-X11")
    assert closure["clues"]["recent_interactions"] == []
    assert closure["steps"][0]["input"] == "평가 장소 A 거기 없던데?"


async def test_opposite_manual_histories_have_same_current_input_and_distinct_context():
    targets, _ = await samples()
    a = next(t for t in targets if t["id"] == "PC-X03")
    b = next(t for t in targets if t["id"] == "PC-X14")
    assert a["before"]["filters"] == b["before"]["filters"]
    assert a["before"]["history"] == b["before"]["history"] == []
    assert a["clues"]["screen"]["places"] == b["clues"]["screen"]["places"]
    assert a["clues"]["recent_interactions"][0]["filters_before"]["candidate_kinds"] == ["cafe"]
    assert b["clues"]["recent_interactions"][0]["filters_before"]["candidate_kinds"] == [
        "restaurant"
    ]
    assert a["steps"][0]["expect"]["candidate_kinds"] == ["cafe"]
    assert b["steps"][0]["expect"]["candidate_kinds"] == ["restaurant"]


async def test_invalid_raw_output_is_preserved_and_cannot_mutate_anchor():
    targets, fixtures = await samples()
    target = copy.deepcopy(next(t for t in targets if t["id"] == "PC-X08"))
    before = copy.deepcopy(target["before"])
    provider = ContextGemini(
        "synthetic-key",
        "mock",
        transport=httpx.MockTransport(
            lambda request: response({"goal": "explain", "reference_index": 0})
        ),
    )
    row = await evaluate_target(target, fixtures, provider, "context-input", 1)
    assert row["prepared"]["receipt"]["code"] == "invalid_plan"
    assert row["prepared"]["state"]["filters"] == before["filters"]
    assert row["provider_calls"][0]["response"]["steps"][0]["arguments"]["reference_index"] == 0
    assert target["before"] == before
