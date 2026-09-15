"""Send the canonical brief once; retain candidate, exact request and reference result."""

import json
import time
from copy import deepcopy

from daengs_backend.services.walk_diary.writing.brief_prompts import BRIEF_POLICY, BRIEF_PROMPTS
from daengs_walk.diary.relational.brief_response import (
    brief_response_schema,
    parse_brief,
    resolve_brief_answer,
)
from daengs_walk.diary.relational.writing_brief import brief_writer_view
from daengs_walk.value_contracts import digest


async def write_brief_task(task, *, send, review=False):
    from daengs_backend.services.walk_diary.writing.relational import (
        FAILURES,
        failure_record,
        review_answer,
    )

    record = {
        "task_id": task.id,
        "scene_id": task.scene_id,
        "stage": task.stage,
        "revision": task.revision,
    }
    started, phase = time.monotonic(), "request"
    try:
        brief = parse_brief(task.payload)
        request, schema = brief_writer_view(brief), brief_response_schema(brief)
        record.update(
            request=deepcopy(request),
            response_schema=deepcopy(schema),
            prompt_revision=digest(BRIEF_PROMPTS[task.stage]),
            policy=BRIEF_POLICY,
            request_revision=digest([BRIEF_POLICY, BRIEF_PROMPTS[task.stage], request, schema]),
        )
        raw = await send(task.stage, deepcopy(request), deepcopy(schema))
        record["raw_text"] = raw
        phase = "references"
        answer = resolve_brief_answer(brief, json.loads(raw))
        record["candidate"] = answer.model_dump(mode="json")
        if review:
            phase = "semantic_review"
            record["semantic_review"] = {}
            required = {brief.required_event.id} if task.stage == "action" else set()
            checked = await review_answer(
                task.stage,
                request,
                record["candidate"],
                required,
                send,
                audit=record["semantic_review"],
            )
            if checked["status"] != "passed":
                record.update(status="failed", failure_phase=phase, error_type="SemanticRejection")
                return record
        record.update(
            status="returned",
            answer=answer.model_dump(mode="json"),
            semantic_status="model_reviewed" if review else "unverified",
        )
    except FAILURES as exc:
        failure_record(record, exc, phase)
    finally:
        record["elapsed_s"] = round(time.monotonic() - started, 3)
    return record
