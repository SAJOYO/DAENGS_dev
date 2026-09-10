"""Compare observed 'yes' reinterpretations against explicit acceptance of the offered plan."""

import argparse
import asyncio
import json
from pathlib import Path

from daengs_place.place.conversation.contract import ConversationState, TurnPlan
from daengs_place.place.tools.changes import apply_changes

from .experiments import PendingProposal
from .fixtures import FixtureSearcher
from .runner import write_json


async def replay(run):
    rows = [
        json.loads(line)
        for line in (run / "observations.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    fixtures = json.loads((run / "fixtures.json").read_text(encoding="utf-8"))
    case = next(
        c
        for c in json.loads((run / "cases.json").read_text(encoding="utf-8"))
        if c["id"] == "PC-E08"
    )
    records = []
    for first in rows:
        if first["case_id"] != "PC-E08" or first["turn"] != 1 or "prepared" not in first:
            continue
        follow = next(
            r
            for r in rows
            if r["case_id"] == "PC-E08"
            and r["turn"] == 2
            and r["repetition"] == first["repetition"]
        )
        arguments = next(
            s["arguments"]
            for c in first["provider_calls"]
            for s in c.get("response", {}).get("steps", [])
            if s.get("type") == "function_call"
        )
        state = ConversationState.model_validate(first["before"])
        pending = PendingProposal.capture(TurnPlan.model_validate(arguments), state, 1)
        confirmed = pending.resolve("accept", state, 1)
        searcher = FixtureSearcher(case["setup"], fixtures)
        result = await searcher(None, apply_changes(state.filters, confirmed.changes))
        refs = [hit.place.key.ref for group in result.groups for hit in group.matched]
        rejected = pending.resolve("reject", state, 1)
        try:
            pending.resolve("accept", state, 2)
        except ValueError:
            stale_blocked = True
        else:
            stale_blocked = False
        records.append(
            {
                "case_id": "PC-E08",
                "repetition": first["repetition"],
                "question": pending.question,
                "offered_plan": arguments,
                "observed_followup_filters": follow["prepared"]["state"]["filters"],
                "observed_followup_refs": follow["returned_refs"],
                "accepted_exact_refs": refs,
                "expected_refs": ["cafe-yes"],
                "acceptance_pass": refs == ["cafe-yes"],
                "rejection_has_no_plan": rejected is None,
                "stale_revision_blocked": stale_blocked,
                "boundary": "observed proposal + structured decision replay; free-text consent classifier not evaluated",
            }
        )
    target = run / "pending-proposal-replay.json"
    if target.exists():
        raise ValueError("replay already exists")
    write_json(target, records)
    print(
        json.dumps(
            {
                "replays": len(records),
                "exact_acceptance_pass": sum(r["acceptance_pass"] for r in records),
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    asyncio.run(replay(parser.parse_args().run))


if __name__ == "__main__":
    main()
