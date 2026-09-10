"""Paired frozen-turn context experiment. Never imported by production."""

import argparse
import asyncio
import hashlib
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from daengs_place.place.conversation.answer import compose_answer
from daengs_place.place.conversation.compiler import canonical, fingerprint
from daengs_place.place.conversation.contract import (
    AnswerRequest,
    ConversationState,
    PrepareRequest,
)
from daengs_place.place.conversation.service import ConversationService, snapshot_hits
from daengs_place.place.providers.gemini import GeminiIntentProposerError

from .checks import assess
from .fixtures import FixtureSearcher, initial_state
from .provider import ObservedGemini
from .runner import DATA, metadata, write_json

ARMS = ("current-input", "context-input")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def filter_view(value):
    return canonical(value)


def context_clues(state, past, now):
    """Allowlist only current visible facts and completed past events, not judgments."""
    hits = {(h.place.key.source, h.place.key.ref): h.place for h in snapshot_hits(state.snapshot)}
    shown = []
    for ordinal, key in enumerate(state.snapshot.display_order if state.snapshot else (), 1):
        place = hits[(key.source, key.ref)]
        shown.append(
            {
                "position": ordinal,
                "key": key.model_dump(mode="json"),
                "name": place.name,
                "kind": place.match.kind,
                "distance_m": place.distance_m,
                "facts": place.facts.model_dump(mode="json"),
            }
        )
    interactions = [
        {
            "mode": row["event"]["action"],
            "user_query": row.get("query"),
            "filters_before": filter_view(row["before"]["filters"]),
            "filters_after": filter_view(row["prepared"]["state"]["filters"]),
            "assistant_text": row["served_answer"]["text"],
        }
        for row in past[-6:]
    ]
    return {
        "screen": {
            "places": shown,
            "selected_place": next(
                (
                    p
                    for p in shown
                    if state.selected and p["key"] == state.selected.model_dump(mode="json")
                ),
                None,
            ),
            "displayed_count": len(shown),
            "groups": [
                {
                    "kind": g.kind,
                    "displayed_count": len(g.matched),
                    "more_matches_than_displayed": g.matched_truncated,
                }
                for g in state.snapshot.result.groups
            ]
            if state.snapshot
            else [],
            "snapshot_created_at": state.snapshot.created_at.isoformat()
            if state.snapshot
            else None,
            "snapshot_age_seconds": (now - state.snapshot.created_at).total_seconds()
            if state.snapshot
            else None,
            "matches_current_filters": state.snapshot.fingerprint == fingerprint(state.filters)
            if state.snapshot
            else False,
        },
        "recent_interactions": interactions,
    }


class ContextGemini(ObservedGemini):
    clues = None

    async def _call(self, payload):
        if self.clues is not None:
            payload = {
                **payload,
                "input": json.dumps(
                    {
                        **json.loads(payload["input"]),
                        "context": self.clues,
                    },
                    ensure_ascii=False,
                ),
            }
        return await super()._call(payload)


async def anchors(spec, source, fixtures):
    cases = {c["id"]: c for c in json.loads((source / "cases.json").read_text(encoding="utf-8"))}
    rows = [
        json.loads(l)
        for l in (source / "observations.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    targets = []
    for target in spec["targets"]:
        timeline = sorted(
            (
                r
                for r in rows
                if r["case_id"] == target["source_case_id"]
                and r["repetition"] == spec["source_repetition"]
            ),
            key=lambda r: r["turn"],
        )
        row = next(r for r in timeline if r["turn"] == target["source_turn"])
        original = cases[target["source_case_id"]]
        step = original["steps"][target["source_turn"] - 1]
        assert step["action"] == "chat" and row["query"] == step["input"]
        state = ConversationState.model_validate(row["before"])
        assert state.pending_proposal is None, "this experiment excludes consent classification"
        now = state.snapshot.result.evaluated_at
        past = [r for r in timeline if r["turn"] < target["source_turn"]]
        setup = original["setup"]
        if target.get("counterfactual_initial_kinds"):
            setup = {**setup, "candidate_kinds": target["counterfactual_initial_kinds"]}
            searcher = FixtureSearcher(setup, fixtures, now=now)
            initial = await initial_state(setup, searcher)
            manual = original["steps"][0]
            assert manual["action"] == "manual" and target["source_turn"] == 2
            prepared = await ConversationService(searcher=searcher, now=lambda now=now: now).prepare(
                None, PrepareRequest(mode="manual", previous=initial, manual=manual["manual"])
            )
            answer = await compose_answer(
                AnswerRequest(
                    query="", committed_revision=prepared.state.revision, prepared=prepared
                )
            )
            state = prepared.state
            past = [
                {
                    "query": None,
                    "event": {"action": "manual"},
                    "before": initial.model_dump(mode="json"),
                    "prepared": prepared.model_dump(mode="json"),
                    "served_answer": answer.model_dump(mode="json"),
                }
            ]
        expected = {**step["expect"], **target.get("expect_override", {})}
        targets.append(
            {
                **target,
                "schema_version": 1,
                "title": original["title"],
                "layer": "context-ablation",
                "setup": setup,
                "steps": [{**step, "expect": expected}],
                "before": state.model_dump(mode="json"),
                "clock": now.isoformat(),
                "clues": context_clues(state, past, now),
                "source_observation_sha256": digest(row),
                "counterfactual": bool(target.get("counterfactual_initial_kinds")),
            }
        )
    return targets


async def evaluate_target(target, fixtures, provider, arm, repetition):
    state = ConversationState.model_validate(target["before"])
    now = datetime.fromisoformat(target["clock"])
    searcher = FixtureSearcher(target["setup"], fixtures, now=now)
    provider.clues = target["clues"] if arm == "context-input" else None
    provider.calls.clear()
    provider.plans.clear()
    provider.drafts.clear()
    step = target["steps"][0]
    record = {
        "case_id": target["id"],
        "variant": arm,
        "repetition": repetition,
        "turn": 1,
        "query": step["input"],
        "before": target["before"],
        "anchor_sha256": digest(target["before"]),
        "source_case_id": target["source_case_id"],
        "source_turn": target["source_turn"],
        "clues": provider.clues,
    }
    try:
        prepared = await ConversationService(provider, searcher=searcher, now=lambda: now).prepare(
            None,
            PrepareRequest(
                mode="chat",
                query=step["input"],
                previous=state,
                visible_order=state.snapshot.display_order,
                visible_selected=state.selected,
            ),
        )
        answer = await compose_answer(
            AnswerRequest(
                query=step["input"], committed_revision=prepared.state.revision, prepared=prepared
            ),
            provider,
        )
        checks = assess(target, step, state, prepared, answer)
        record.update(
            {
                "status": "fail"
                if any(c["status"] == "fail" for c in checks)
                else "review_required",
                "checks": checks,
                "prepared": prepared.model_dump(mode="json"),
                "served_answer": answer.model_dump(mode="json"),
                "returned_refs": [h.place.key.ref for h in snapshot_hits(prepared.state.snapshot)],
            }
        )
    except (GeminiIntentProposerError, ValueError, RuntimeError, TimeoutError) as error:
        record.update(status="blocked", error_type=type(error).__name__)
    record.update(
        provider_calls=list(provider.calls),
        plans=list(provider.plans),
        search_calls=len(searcher.calls),
    )
    return record


async def execute(args):
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if spec["schema_version"] != 1 or len({t["id"] for t in spec["targets"]}) != len(
        spec["targets"]
    ):
        raise ValueError("unsupported spec or duplicate target IDs")
    source = DATA / "runs" / spec["source_run"]
    fixtures_path = source / "fixtures.json"
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    targets = await anchors(spec, source, fixtures)
    if not args.live:
        for target in targets:
            print(target["id"], target["source_case_id"], target["source_turn"])
        return
    key = os.environ.get("GEMINI_API_KEY", "")
    if args.key_file:
        match = re.search(
            r"(?im)^\s*(?:GEMINI_API_KEY\s*=|gemini\s*:)\s*(\S+)",
            args.key_file.read_text(encoding="utf-8-sig"),
        )
        key = match.group(1).strip("\"'") if match else ""
    if not key:
        raise ValueError("GEMINI_API_KEY or --key-file required")
    meta = metadata(args.spec, fixtures_path, args.model, args.repeat, [t["id"] for t in targets])
    meta.update(
        variant="paired-context-ablation-v1",
        arms=list(ARMS),
        source_run=spec["source_run"],
        source_observations_sha256=hashlib.sha256(
            (source / "observations.jsonl").read_bytes()
        ).hexdigest(),
        method="frozen pre-turn state; only input.context differs; no trajectory propagation",
        boundary="live Gemini + fixed production prompt/schema/policy/answer + synthetic search",
        min_call_interval_seconds=args.interval,
        ordering="alternate AB/BA by target index and repetition; shared global pacing",
        anchor_semantics="14 independent turn targets, not complete dialogue success",
    )
    run = (
        DATA
        / "runs"
        / (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + meta["code_sha"][:7]
            + "-"
            + meta["run_id"]
        )
    )
    run.mkdir(exist_ok=False)
    write_json(run / "metadata.json", meta)
    write_json(run / "cases.json", targets)
    write_json(run / "fixtures.json", fixtures)
    write_json(run / "spec.json", spec)
    provider = ContextGemini(key, args.model, interval=args.interval)
    counts = Counter()
    print("run:", run, flush=True)
    with (run / "observations.jsonl").open("x", encoding="utf-8") as out:
        for repetition in range(1, args.repeat + 1):
            for index, target in enumerate(targets):
                order = ARMS if (index + repetition) % 2 else ARMS[::-1]
                for arm in order:
                    row = await evaluate_target(target, fixtures, provider, arm, repetition)
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    out.flush()
                    counts[row["status"]] += 1
                    print(target["id"], repetition, arm, row["status"], flush=True)
    write_json(
        run / "summary.json",
        {
            "completed_at": datetime.now(UTC).isoformat(),
            "turn_status_counts": dict(counts),
            "whole_case_pass_count": 0,
            "semantic_review": "pending",
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DATA / "context-ablation.v1.json")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    args = parser.parse_args()
    if args.repeat < 1 or args.interval < 0:
        parser.error("positive repeat and nonnegative interval required")
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
