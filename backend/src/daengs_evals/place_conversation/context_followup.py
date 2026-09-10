"""Small follow-up of observed context interference; production remains unchanged."""

import argparse
import asyncio
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .context_ablation import ContextGemini, evaluate_target
from .provider import ObservedGemini
from .runner import DATA, metadata, write_json


def arrange(payload, clues, variant):
    data = json.loads(payload["input"])
    if variant == "query-last":
        query = data.pop("query")
        data["context"] = clues
        data["query"] = query
    elif variant == "no-duplicate-past-query":
        data["context"] = {
            **clues,
            "recent_interactions": [
                {k: v for k, v in event.items() if k != "user_query"}
                for event in clues["recent_interactions"]
            ],
        }
    else:
        raise ValueError("unknown context arrangement")
    return {**payload, "input": json.dumps(data, ensure_ascii=False)}


class ArrangedGemini(ContextGemini):
    variant = "query-last"

    async def _call(self, payload):
        return await ObservedGemini._call(self, arrange(payload, self.clues, self.variant))


async def execute(args):
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    source = DATA / "runs" / spec["source_run"]
    if not (source / "summary.json").exists():
        raise ValueError("wait for the parent experiment to finish")
    all_targets = json.loads((source / "cases.json").read_text(encoding="utf-8"))
    targets = [t for t in all_targets if t["id"] in spec["target_ids"]]
    if {t["id"] for t in targets} != set(spec["target_ids"]):
        raise ValueError("missing target")
    fixtures_path = source / "fixtures.json"
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    if not args.live:
        print(json.dumps(spec))
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
    meta = metadata(args.spec, fixtures_path, args.model, args.repeat, spec["target_ids"])
    meta.update(
        variant="context-arrangement-followup-v1",
        arms=spec["arms"],
        source_run=spec["source_run"],
        method="same frozen anchors; only query key position OR duplicate past query omission",
        min_call_interval_seconds=5,
        ordering="alternate arm order by target and repetition",
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
    for name, value in (
        ("metadata", meta),
        ("cases", targets),
        ("fixtures", fixtures),
        ("spec", spec),
    ):
        write_json(run / (name + ".json"), value)
    provider = ArrangedGemini(key, args.model, interval=5)
    counts = Counter()
    print("run:", run, flush=True)
    with (run / "observations.jsonl").open("x", encoding="utf-8") as out:
        for repetition in range(1, args.repeat + 1):
            for index, target in enumerate(targets):
                order = spec["arms"] if (index + repetition) % 2 else spec["arms"][::-1]
                for variant in order:
                    provider.variant = variant
                    row = await evaluate_target(
                        target, fixtures, provider, "context-input", repetition
                    )
                    row["variant"] = variant
                    if row["provider_calls"]:
                        row["clues"] = json.loads(row["provider_calls"][0]["request"]["input"])[
                            "context"
                        ]
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    out.flush()
                    counts[row["status"]] += 1
                    print(target["id"], repetition, variant, row["status"], flush=True)
    write_json(
        run / "summary.json",
        {
            "completed_at": datetime.now(UTC).isoformat(),
            "turn_status_counts": dict(counts),
            "semantic_review": "pending",
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DATA / "context-followup.v1.json")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("positive repeat required")
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
