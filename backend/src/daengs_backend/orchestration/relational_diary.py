"""Runnable skeleton: prepare -> separate writers -> assemble -> title. No API switch."""

import json

import httpx

from daengs_backend.services.walk_diary.preparation.relational import prepare_relational_diary
from daengs_backend.services.walk_diary.writing.relational import (
    generate_relation_part,
    write_relational_diary,
)
from daengs_walk.diary.relational.assembly import assemble_receipt


async def generate_relational_skeleton(base, *, scene_ids=None, road_snapshots=(), send=None):
    from google.genai.errors import APIError

    prepared = prepare_relational_diary(base, scene_ids=scene_ids, road_snapshots=road_snapshots)
    written = await write_relational_diary(prepared, send=send)
    receipt = assemble_receipt(prepared, written)
    # Only confirmed generated parts and computed observations; no originals or prior prompts.
    title_input = {
        "scenes": [
            {"body": c["body"], "observations": [o["text"] for o in c["movement_observations"]]}
            for c in receipt["cards"]
        ]
    }
    title = {"status": "not_requested", "text": "산책 기록"}
    if any(c["body"] or c["movement_observations"] for c in receipt["cards"]):
        try:
            raw = await (send or generate_relation_part)(
                "title",
                title_input,
                {
                    "type": "object",
                    "properties": {"title": {"type": "string", "maxLength": 30}},
                    "required": ["title"],
                },
            )
            title["raw_text"] = raw
            value = json.loads(raw)["title"]
            if not isinstance(value, str) or not value.strip() or len(value) > 30:
                raise ValueError("invalid title")
            title.update(status="returned", text=value)
        except (APIError, httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            title.update(status="failed", error_type=type(exc).__name__)
    receipt["title"] = title
    return {"prepared": prepared, "receipt": receipt}
