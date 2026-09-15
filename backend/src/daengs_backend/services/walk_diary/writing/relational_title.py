"""One title and optional review over the same frozen, scene-owned body input."""

import json
from copy import deepcopy

from daengs_backend.services.walk_diary.writing.relational import (
    FAILURES,
    PROMPTS,
    failure_record,
    review_answer,
)
from daengs_walk.diary.relational.title_context import (
    TitleAnswer,
    title_context,
    title_request_revision,
)
from daengs_walk.value_contracts import digest


async def write_relational_title(receipt, *, send, review=True):
    if type(review) is not bool:
        raise TypeError("title review policy must be explicit")
    context = title_context(receipt)
    request = context.model_dump(mode="json")
    schema = TitleAnswer.model_json_schema()
    prompt_revision = digest(PROMPTS["title"])
    title = {
        "status": "not_requested",
        "text": "산책 기록",
        "request": deepcopy(request),
        "content_revision": digest(context),
        "response_schema": deepcopy(schema),
        "prompt_revision": prompt_revision,
        "review_enabled": review,
        "request_revision": title_request_revision(prompt_revision, request, schema),
    }
    if not context.scenes:
        return title
    phase = "request"
    try:
        raw = await send("title", deepcopy(request), deepcopy(schema))
        title["raw_text"] = raw
        value = TitleAnswer.model_validate(json.loads(raw)).title
        title["candidate"] = value
        if review:
            phase = "semantic_review"
            title["semantic_review"] = {}
            checked = await review_answer(
                "title",
                request,
                {"text": value, "evidence_ids": context.citation_ids()},
                set(),
                send,
                audit=title["semantic_review"],
            )
            if checked["status"] != "passed":
                raise ValueError("title semantic review rejected")
        title.update(
            status="returned",
            text=value,
            semantic_status="model_reviewed" if review else "unverified",
        )
    except FAILURES as exc:
        failure_record(title, exc, phase)
    return title
