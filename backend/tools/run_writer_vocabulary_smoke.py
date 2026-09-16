"""Two synthetic endpoint cases through the production writer and Gemini transport."""

import argparse
import asyncio
import json
import sys
from pathlib import Path


async def run(output):
    from daengs_backend.services.walk_diary.writing.brief_writer import write_brief_task
    from daengs_backend.services.walk_diary.writing.relational import (
        MODEL,
        generate_relation_part,
        writing_prompt,
    )
    from daengs_backend.services.walk_diary.writing.relational_transport import CallCoordinator
    from daengs_walk.diary.relational.brief_publication import validate_brief_result
    from daengs_walk.diary.relational.contracts import WriterTask, writer_task
    from daengs_walk.diary.relational.writer_view import publication_writer_view
    from daengs_walk.diary.relational.writing_brief import build_space_brief
    from tests.walk.diary.test_writing_brief import case, context, snapshot

    coordinator = CallCoordinator(
        generate_relation_part,
        minimum_interval_s=10,
        max_calls=2,
        call_timeout_s=20,
        total_timeout_s=60,
    )
    report = {
        "scope": "Synthetic snapshots; production write_brief_task, prompt, SDK and receipt validation; no DB or APP",
        "model": MODEL,
        "minimum_interval_s": 10,
        "automatic_retries": 0,
        "cases": [],
    }
    for distance in (30, 200):
        walk, scenes, positions = case()
        ctx = context(
            snapshot(scenes[0], walk), snapshot(scenes[1], walk, distance=distance), positions
        )
        brief = build_space_brief(ctx)
        task = WriterTask.model_validate(writer_task("space", scenes[1].id, brief))
        result = await write_brief_task(task, send=coordinator)
        validated = validate_brief_result(task, result)
        row = {
            "current_distance_m": distance,
            "task": task.model_dump(mode="json"),
            "previous_policy_request": publication_writer_view(brief, "single-writing-brief-v2"),
            "prompt": writing_prompt("space", result["request"]),
            "result": result,
            "accepted": validated is not None,
        }
        report["cases"].append(row)
        report["calls"] = coordinator.trace
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "distance": distance,
                    "status": result["status"],
                    "text": result.get("answer", {}).get("text"),
                    "relationship": result["request"]["relation_slots"]["proximity"][0][
                        "relationship"
                    ],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if coordinator.stopped:
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from run_diary_route_scenario import configure

    configure(args.env)
    asyncio.run(run(args.output))
