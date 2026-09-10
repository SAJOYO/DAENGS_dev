"""Replay observed proposals under narrowly scoped alternatives; no model or DB calls."""

import argparse
import asyncio
import copy
import json
from pathlib import Path

from daengs_place.place.conversation.answer import compose_answer
from daengs_place.place.conversation.contract import (
    AnswerDraft,
    AnswerRequest,
    ConversationState,
    PrepareRequest,
    TurnPlan,
)
from daengs_place.place.conversation.service import ConversationService, snapshot_hits

from .experiments import compile_replacement_branches
from .fixtures import FixtureSearcher, initial_state
from .runner import DATA, read_cases, write_json


class FixedPlanner:
    def __init__(self, plan):
        self.next = plan

    async def plan(self, request):
        return self.next


async def evidence_gap(run):
    """Show that false and unknown become identical answer inputs, even with opaque IDs."""
    fixtures = json.loads((DATA / "fixtures.v1.json").read_text(encoding="utf-8"))
    setup = copy.deepcopy(read_cases(DATA / "cases.v1.jsonl")[0]["setup"])
    setup["selected"] = "opaque-place-001"
    records = []
    for parking in (False, None):
        variant = copy.deepcopy(fixtures)
        variant["standard"][0].update(ref=setup["selected"], name="테스트 장소", parking=parking)
        searcher = FixtureSearcher(setup, variant)
        state = await initial_state(setup, searcher)
        prepared = await ConversationService(
            FixedPlanner(TurnPlan(goal="explain")),
            searcher=searcher,
        ).prepare(None, PrepareRequest(mode="chat", query="이 장소 주차 가능해?", previous=state))
        receipt = prepared.receipt.model_dump(mode="json")
        # Snapshot identities differ, but carry no parking semantics.
        receipt["snapshot_id"] = "<same-opaque-snapshot>"
        records.append({"underlying_parking": parking, "answer_receipt": receipt})
    result = {
        "variant": "false-versus-unknown-evidence",
        "records": records,
        "answer_inputs_identical": records[0]["answer_receipt"] == records[1]["answer_receipt"],
        "boundary": "production explanation receipt, opaque selected ID; no model call",
        "implication": "The answer layer cannot distinguish confirmed false from missing evidence.",
    }
    target = run / "evidence-gap.json"
    if target.exists():
        raise ValueError("evidence gap observation already exists")
    write_json(target, result)
    print(json.dumps({"answer_inputs_identical": result["answer_inputs_identical"]}))


async def replay(run):
    cases = {
        case["id"]: case for case in json.loads((run / "cases.json").read_text(encoding="utf-8"))
    }
    fixtures = json.loads((run / "fixtures.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (run / "observations.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    records = []
    for row in rows:
        if row.get("variant") != "production-baseline" or "before" not in row:
            continue
        raw = [
            step["arguments"]
            for call in row.get("provider_calls", [])
            for step in call.get("response", {}).get("steps", [])
            if step.get("type") == "function_call"
        ]
        if len(raw) != 1:
            continue
        proposed = raw[0]
        variant = None
        if row["case_id"] == "PC-E05" and proposed.get("changes", {}).get("upsert_any"):
            anonymous = [
                {"all": [{k: v for k, v in atom.items() if k != "id"} for atom in branch["all"]]}
                for branch in proposed["changes"]["upsert_any"]
            ]
            proposed = json.loads(json.dumps(proposed))
            proposed["changes"]["upsert_any"] = compile_replacement_branches(anonymous)
            variant = "server-assigned-expression-ids"
        elif proposed.get("question", "").strip() and proposed["goal"] != "clarify":
            proposed = {"goal": "clarify", "question": proposed["question"]}
            variant = "question-gate"
        if variant is None:
            continue
        before = ConversationState.model_validate(row["before"])
        searcher = FixtureSearcher(
            cases[row["case_id"]]["setup"], fixtures, before.snapshot.result.evaluated_at
        )
        plan = TurnPlan.model_validate(proposed)
        prepared = await ConversationService(
            FixedPlanner(plan),
            searcher=searcher,
            now=lambda searcher=searcher: searcher.now,
        ).prepare(
            None,
            PrepareRequest(
                mode="chat",
                query=row["query"],
                previous=before,
                visible_order=before.snapshot.display_order,
                visible_selected=before.selected,
            ),
        )
        records.append(
            {
                "case_id": row["case_id"],
                "repetition": row["repetition"],
                "turn": row["turn"],
                "variant": variant,
                "original_plan": raw[0],
                "alternative_plan": proposed,
                "receipt": prepared.receipt.model_dump(mode="json"),
                "returned_refs": [h.place.key.ref for h in snapshot_hits(prepared.state.snapshot)],
                "filters_preserved": prepared.state.filters == before.filters,
                "boundary": "captured-plan replay; not a fresh semantic-compiler LLM experiment",
            }
        )
    # Probe free prose validation with a controlled, deliberately ungrounded draft.
    case = next(c for c in read_cases(DATA / "cases.v1.jsonl") if c["id"] == "PC-E16")
    searcher = FixtureSearcher(case["setup"], fixtures)
    state = await initial_state(case["setup"], searcher)
    prepared = await ConversationService(
        FixedPlanner(TurnPlan(goal="explain")),
        searcher=searcher,
    ).prepare(None, PrepareRequest(mode="chat", query="거기 조용하고 무료지?", previous=state))
    draft = AnswerDraft(
        text=prepared.receipt.evidence["place"] + "는 조용하고 무료예요.", evidence_ids=("place",)
    )

    class Injected:
        async def answer(self, request):
            return draft

    request = AnswerRequest(query="거기 조용하고 무료지?", committed_revision=1, prepared=prepared)
    served = await compose_answer(request, Injected())
    # Rendering only server evidence has no arbitrary claim channel.
    constrained = " ".join(
        prepared.receipt.evidence[key] for key in ("place", "distance", "parking")
    )
    records.append(
        {
            "case_id": "PC-E16",
            "variant": "controlled-prose-injection",
            "draft": draft.model_dump(mode="json"),
            "evidence": prepared.receipt.evidence,
            "served": served.model_dump(mode="json"),
            "unsupported_draft_accepted": served.source == "llm",
            "evidence_only_render": constrained,
            "boundary": "fault injection; does not claim the live model generated this draft",
        }
    )
    write_json(run / "diagnostics.json", records)
    print(json.dumps({"diagnostic_records": len(records)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--evidence-gap", action="store_true")
    args = parser.parse_args()
    if args.evidence_gap:
        asyncio.run(evidence_gap(args.run))
        return
    if (args.run / "diagnostics.json").exists():
        parser.error("diagnostics already exists; never overwrite observations")
    asyncio.run(replay(args.run))


if __name__ == "__main__":
    main()
