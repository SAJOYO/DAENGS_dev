"""Real title-only comparison over a frozen receipt; no body or public API regeneration."""

import argparse
import asyncio
import gzip
import json
import time
from copy import deepcopy
from pathlib import Path


async def run(args):
    from run_diary_route_scenario import configure

    configure(args.env_file)
    from daengs_backend.services.walk_diary.relational_execution import MODEL
    from daengs_backend.services.walk_diary.writing.relational import (
        FAILURES,
        PROMPTS,
        failure_record,
        generate_relation_part,
        review_answer,
    )
    from daengs_backend.services.walk_diary.writing.relational_title import write_relational_title
    from daengs_backend.services.walk_diary.writing.relational_transport import CallCoordinator
    from daengs_walk.diary.relational.publication import validate_publication
    from daengs_walk.diary.relational.title_context import (
        TITLE_CONTRACT,
        TitleAnswer,
        validate_title_publication,
    )
    from daengs_walk.value_contracts import digest

    raw = args.receipt.read_bytes()
    source = json.loads(gzip.decompress(raw) if args.receipt.suffix == ".gz" else raw)
    body = source.get("payload", source)
    receipt = deepcopy(body.get("receipt", body))
    validate_publication(receipt)
    args.output.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (args.output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    calls, results = [], {}
    condition = "legacy" if args.compare_legacy else "readmodel"

    async def send(stage, payload, schema):
        call = {"condition": condition, "stage": stage, "request": deepcopy(payload)}
        started = time.monotonic()
        try:
            value = await generate_relation_part(stage, payload, schema)
            call["raw_text"] = value
            print(
                json.dumps(
                    {"condition": condition, "stage": stage, "raw": value}, ensure_ascii=False
                ),
                flush=True,
            )
            return value
        except Exception as exc:
            call["error_type"] = type(exc).__name__
            raise
        finally:
            call["elapsed_s"] = round(time.monotonic() - started, 3)
            calls.append(call)
            save("calls.json", calls)

    coordinator = CallCoordinator(
        send,
        minimum_interval_s=10,
        max_calls=4 if args.compare_legacy else 2,
        call_timeout_s=15,
        total_timeout_s=120,
    )
    save(
        "manifest.json",
        {
            "model": MODEL,
            "source_receipt_digest": digest(receipt),
            "minimum_interval_s": 10,
            "max_calls": coordinator.max_calls,
            "new_body_calls": 0,
            "new_public_api_calls": 0,
            "prompt_overrides": False,
            "title_prompt": PROMPTS["title"],
            "review_prompt": PROMPTS["review"],
            "comparison": "same body and output schema; legacy flattened input vs scene readmodel",
        },
    )
    if args.compare_legacy:
        scenes = []
        for card in receipt["cards"]:
            if card["body"]:
                scenes.append({"id": f"scene:{len(scenes) + 1}", "body": card["body"]})
            for observation in card["movement_observations"]:
                scenes.append(
                    {"id": f"scene:{len(scenes) + 1}", "device_observation": observation["text"]}
                )
        request = {"scenes": scenes}
        legacy = {"status": "not_requested", "request": request}
        phase = "request"
        if scenes:
            try:
                value = await coordinator(
                    "title", deepcopy(request), TitleAnswer.model_json_schema()
                )
                legacy["raw_text"] = value
                text = TitleAnswer.model_validate(json.loads(value)).title
                legacy["candidate"] = text
                phase = "semantic_review"
                legacy["semantic_review"] = {}
                checked = await review_answer(
                    "title",
                    request,
                    {"text": text, "evidence_ids": [s["id"] for s in scenes]},
                    set(),
                    coordinator,
                    audit=legacy["semantic_review"],
                )
                if checked["status"] != "passed":
                    raise ValueError("legacy title rejected")
                legacy.update(status="returned", text=text)
            except FAILURES as exc:
                failure_record(legacy, exc, phase)
        results["legacy"] = legacy
        save("results.json", results)
    condition = "readmodel"
    receipt["title"] = await write_relational_title(receipt, send=coordinator)
    receipt["title_contract"] = TITLE_CONTRACT
    validate_title_publication(receipt)
    results["readmodel"] = receipt["title"]
    save("results.json", results)
    save("execution.json", {"calls": coordinator.trace, "automatic_retries": 0})
    print(
        json.dumps(
            {
                "done": True,
                "calls": coordinator.calls,
                "titles": {
                    key: {"status": value["status"], "candidate": value.get("candidate")}
                    for key, value in results.items()
                },
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare-legacy", action="store_true")
    asyncio.run(run(parser.parse_args()))
