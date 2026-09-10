"""Run versioned synthetic conversations; preserve every completed observation."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from daengs_place.place.conversation.answer import compose_answer
from daengs_place.place.conversation.contract import AnswerRequest, PrepareRequest
from daengs_place.place.conversation.intent import Interpretation
from daengs_place.place.conversation.service import ConversationService, snapshot_hits
from daengs_place.place.providers.gemini import GeminiIntentProposerError

from .checks import assess
from .fixtures import FixtureSearcher, initial_state
from .provider import ObservedGemini

DATA = Path(__file__).resolve().parents[3] / "evals/place_conversation"
REPO = DATA.parents[2]


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_cases(path):
    cases = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)) or any(case["schema_version"] != 1 for case in cases):
        raise ValueError("duplicate IDs or unsupported scenario schema")
    return cases


def metadata(cases_path, fixtures_path, model, repeat, ids):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()

    sources = {}
    for directory in (
        REPO / "backend/src/daengs_place",
        REPO / "backend/src/daengs_evals/place_conversation",
    ):
        for file in sorted(directory.rglob("*.py")):
            sources[file.relative_to(REPO).as_posix()] = hashlib.sha256(
                file.read_bytes()
            ).hexdigest()
    return {
        "schema_version": 1,
        "run_id": uuid4().hex[:10],
        "started_at": datetime.now(UTC).isoformat(),
        "code_sha": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "source_sha256": sources,
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "fixtures_sha256": hashlib.sha256(fixtures_path.read_bytes()).hexdigest(),
        "model": model,
        "repeat": repeat,
        "case_ids": ids,
        "variant": "production-policy-v1",
        "seed": None,
        "boundary": "live Gemini + production prepare/answer + synthetic search; no HTTP/DB",
        "revision_scope": "local turn ordinal, NOT Redis CAS verification",
        "semantic_review": "pending; see reviews.jsonl separately",
    }


async def run_case(case, fixtures, provider, repetition):
    searcher = FixtureSearcher(case["setup"], fixtures)
    state = await initial_state(case["setup"], searcher)
    service = ConversationService(provider, searcher=searcher, now=lambda: searcher.now)
    for ordinal, step in enumerate(case["steps"], 1):
        record = {
            "case_id": case["id"],
            "repetition": repetition,
            "turn": ordinal,
            "variant": "production-policy-v1",
            "layer": case["layer"],
            "query": step.get("input"),
            "event": {k: v for k, v in step.items() if k not in {"expect", "review"}},
            "before": state.model_dump(mode="json"),
        }
        if step["action"] not in {"chat", "manual"}:
            record.update(
                status="not_run", reason="Controlled API scenario requires separate HTTP harness."
            )
            yield record
            break
        call_start, plan_start, draft_start = (
            len(provider.calls),
            len(provider.plans),
            len(provider.drafts),
        )
        search_start = len(searcher.calls)
        started = perf_counter()
        try:
            request = PrepareRequest(
                mode=step["action"],
                query=step.get("input", ""),
                manual=step.get("manual"),
                previous=state,
                visible_order=state.snapshot.display_order if state.snapshot else (),
                visible_selected=state.selected,
            )
            prepared = await service.prepare(None, request)
            answer_request = AnswerRequest(
                query=step.get("input", ""),
                committed_revision=prepared.state.revision,
                prepared=prepared,
            )
            answer = await compose_answer(answer_request, provider)
            # Acceptance executes the stored candidate without another semantic plan.
            # A new independent request also clears pending, but must NOT be graded
            # against the cancelled proposal's candidate.
            accepting_pending = (
                step["action"] == "chat"
                and state.pending_proposal is not None
                and prepared.receipt.action == "execute"
                and len(provider.plans) == plan_start
            )
            checks = assess(
                case, step, state, prepared, answer, accepting_pending=accepting_pending
            )
            record.update(
                status="fail" if any(c["status"] == "fail" for c in checks) else "review_required",
                checks=checks,
                prepared=prepared.model_dump(mode="json"),
                served_answer=answer.model_dump(mode="json"),
                returned_refs=[h.place.key.ref for h in snapshot_hits(prepared.state.snapshot)],
                result_delta={
                    "added": sorted(
                        {h.place.key.ref for h in snapshot_hits(prepared.state.snapshot)}
                        - {h.place.key.ref for h in snapshot_hits(state.snapshot)}
                    ),
                    "removed": sorted(
                        {h.place.key.ref for h in snapshot_hits(state.snapshot)}
                        - {h.place.key.ref for h in snapshot_hits(prepared.state.snapshot)}
                    ),
                },
            )
            state = prepared.state
        except (GeminiIntentProposerError, ValueError, RuntimeError, TimeoutError) as error:
            record.update(status="blocked", error_type=type(error).__name__)
        record.update(
            provider_calls=provider.calls[call_start:],
            plans=provider.plans[plan_start:],
            answer_drafts=provider.drafts[draft_start:],
            search_calls=len(searcher.calls) - search_start,
            latency_ms=round((perf_counter() - started) * 1000),
        )
        yield record
        if record["status"] == "blocked":
            # Never grade follow-ups against a fabricated prior turn.
            for skipped in range(ordinal + 1, len(case["steps"]) + 1):
                yield {
                    "case_id": case["id"],
                    "repetition": repetition,
                    "turn": skipped,
                    "status": "not_run",
                    "reason": "Earlier turn blocked.",
                }
            break


async def direct_answer_probe(case, fixtures, provider, repetition):
    """Bypass planning deliberately; a clarifying plan must not hide answer-layer behavior."""

    class Explain:
        async def plan(self, request):
            return Interpretation(goal="explain", asked_attributes=("quiet", "free"))

    searcher = FixtureSearcher(case["setup"], fixtures)
    state = await initial_state(case["setup"], searcher)
    service = ConversationService(Explain(), searcher=searcher, now=lambda: searcher.now)
    query = case["steps"][0]["input"]
    prepared = await service.prepare(None, PrepareRequest(mode="chat", previous=state, query=query))
    start, drafts = len(provider.calls), len(provider.drafts)
    request = AnswerRequest(query=query, committed_revision=1, prepared=prepared)
    answer = await compose_answer(request, provider)
    return {
        "case_id": case["id"],
        "variant": "direct-answer",
        "repetition": repetition,
        "turn": 1,
        "query": query,
        "prepared": prepared.model_dump(mode="json"),
        "served_answer": answer.model_dump(mode="json"),
        "answer_drafts": provider.drafts[drafts:],
        "provider_calls": provider.calls[start:],
        "status": "review_required",
        "search_calls": 0,
    }


async def execute(args):
    cases = read_cases(args.cases)
    if args.ids:
        wanted = set(args.ids.split(","))
        if wanted - {case["id"] for case in cases}:
            raise ValueError("unknown case ID")
        cases = [case for case in cases if case["id"] in wanted]
    if not args.live:
        for case in cases:
            print(case["id"], case["layer"], case["title"])
        return
    key = os.environ.get("GEMINI_API_KEY", "")
    if args.key_file:
        text = args.key_file.read_text(encoding="utf-8-sig")
        match = re.search(r"(?im)^\s*(?:GEMINI_API_KEY\s*=|gemini\s*:)\s*(\S+)", text)
        key = match.group(1).strip("\"'") if match else ""
    if not key:
        raise ValueError("GEMINI_API_KEY or --key-file required; never pass a literal key")
    fixtures = json.loads(args.fixtures.read_text(encoding="utf-8"))
    meta = metadata(
        args.cases, args.fixtures, args.model, args.repeat, [case["id"] for case in cases]
    )
    run = args.output / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + meta["code_sha"][:7]
        + "-"
        + meta["run_id"]
    )
    run.mkdir(parents=True, exist_ok=False)
    write_json(run / "metadata.json", meta)
    # Preserve source data with the run so later case edits cannot rewrite expectations.
    write_json(run / "cases.json", cases)
    write_json(run / "fixtures.json", fixtures)
    meta["min_call_interval_seconds"] = args.interval
    meta["variant"] = args.variant
    write_json(run / "metadata.json", meta)
    provider = ObservedGemini(key, args.model, interval=args.interval)
    counts = Counter()
    print("run:", run, flush=True)
    with (run / "observations.jsonl").open("x", encoding="utf-8") as output:
        for repetition in range(1, args.repeat + 1):
            for case in cases:
                async for record in run_case(case, fixtures, provider, repetition):
                    record["variant"] = args.variant
                    output.write(json.dumps(record, ensure_ascii=False) + "\n")
                    output.flush()
                    counts[record["status"]] += 1
                    print(case["id"], repetition, record["turn"], record["status"], flush=True)
                if case["id"] == "PC-E16":
                    record = await direct_answer_probe(case, fixtures, provider, repetition)
                    output.write(json.dumps(record, ensure_ascii=False) + "\n")
                    output.flush()
                    counts[record["status"]] += 1
                provider.calls.clear()
                provider.plans.clear()
                provider.drafts.clear()
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
    parser.add_argument(
        "--live", action="store_true", help="Make paid Gemini calls. Otherwise list cases."
    )
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument(
        "--interval", type=float, default=5, help="Minimum seconds between provider calls"
    )
    parser.add_argument("--ids", help="Comma-separated case IDs")
    parser.add_argument(
        "--variant",
        choices=("production-policy-v1",),
        default="production-policy-v1",
    )
    parser.add_argument("--cases", type=Path, default=DATA / "cases.v1.jsonl")
    parser.add_argument("--fixtures", type=Path, default=DATA / "fixtures.v1.json")
    parser.add_argument("--output", type=Path, default=DATA / "runs")
    args = parser.parse_args()
    if args.repeat < 1 or args.interval < 0:
        parser.error("--repeat must be positive and --interval nonnegative")
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
