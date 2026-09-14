"""Repeatable bookmark-intent experiment. Synthetic pool; no member writes or database."""

import argparse
import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from daengs_place.place.conversation.contract import PrepareRequest
from daengs_place.place.conversation.render import render_answer
from daengs_place.place.conversation.service import ConversationService

from .fixtures import FixtureSearcher, initial_state
from .provider import ObservedGemini
from .runner import DATA, write_json


async def run(args):
    match = re.search(
        r"(?im)^\s*(?:GEMINI_API_KEY\s*=|gemini\s*:)\s*(\S+)",
        args.key_file.read_text(encoding="utf-8-sig"),
    )
    if not match:
        raise ValueError("key file contains no Gemini key")
    model = ObservedGemini(match[1].strip("\"'"), args.model, interval=3)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        if not wanted <= {case["id"] for case in cases}:
            raise ValueError("unknown case ID")
        cases = [case for case in cases if case["id"] in wanted]
    setup = {
        "candidate_kinds": ["cafe"],
        "origin": {"lat": 37.5, "lng": 127.0},
        "radius_m": 3000,
        "name_query": "",
        "dogs": [],
        "parking": "none",
        "selected": "A",
        "snapshot_age_seconds": 0,
    }
    fixtures = {
        "defaults": {"source": "kcisa", "address": "평가용 가상 주소"},
        "standard": [
            {"ref": ref, "name": f"평가 카페 {ref}", "kind": "cafe", "parking": True}
            for ref in ("A", "B", "C")
        ],
    }
    directory = args.output / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory.mkdir(parents=True)
    write_json(directory / "cases.json", cases)
    sources = Path(__file__).resolve().parents[2] / "daengs_place" / "place" / "conversation"
    write_json(
        directory / "metadata.json",
        {
            "model": args.model,
            "repeat": args.repeat,
            "setup": setup,
            "fixtures": fixtures,
            "boundary": "real Gemini + production policy; synthetic pool, no member writes",
            "source_sha256": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources.glob("*.py")
            },
        },
    )
    outcomes = []
    for repetition in range(args.repeat):
        for case in cases:
            search = FixtureSearcher(setup, fixtures)
            before = await initial_state(setup, search)
            start = len(model.calls)
            row = {**case, "repetition": repetition + 1}
            try:
                result = await ConversationService(model, searcher=search).prepare(
                    None,
                    PrepareRequest(
                        mode="chat",
                        query=case["query"],
                        previous=before,
                        bookmark_commands="v1",
                        visible_order=before.snapshot.display_order,
                        visible_selected=before.selected,
                    ),
                )
                command = result.receipt.bookmark_command
                no_edit = (
                    result.state.filters == before.filters
                    and result.state.exploration.excluded == before.exploration.excluded
                )
                unchanged = (
                    no_edit
                    and result.state.snapshot == before.snapshot
                    and result.state.exploration == before.exploration
                )
                expected = case["expect"]
                passed = (
                    bool(
                        command
                        and command.saved == (expected == "save")
                        and command.key.ref == case["ref"]
                        and unchanged
                    )
                    if expected in {"save", "remove"}
                    else command is None and no_edit and result.receipt.browse == "next"
                    if expected == "next"
                    else command is None
                    and unchanged
                    and (expected != "clarify" or result.receipt.action == "clarify")
                )
                row.update(
                    status="pass" if passed else "fail",
                    receipt=result.receipt.model_dump(mode="json"),
                    answer=render_answer(result.receipt),
                    before=before.model_dump(mode="json"),
                    after=result.state.model_dump(mode="json"),
                )
            except Exception as error:  # noqa: BLE001 -- preserve failures without logging secrets
                row.update(status="incomplete", error_type=type(error).__name__)
            row["provider_calls"] = model.calls[start:]
            outcomes.append(row)
            write_json(directory / "observations.json", outcomes)
            print(f"{case['id']} repeat={repetition + 1} {row['status']}", flush=True)
    print(str(directory), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--repeat", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--only", help="Comma-separated case IDs for a bounded follow-up")
    parser.add_argument("--cases", type=Path, default=DATA / "bookmark-cases.json")
    parser.add_argument("--output", type=Path, default=DATA / "bookmark-runs")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
