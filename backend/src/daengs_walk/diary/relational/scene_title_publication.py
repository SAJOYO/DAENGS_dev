"""Read-time scene/body/title binding, without current prompts or model calls."""

import json

from daengs_walk.diary.relational.scene_title_context import (
    SCENE_TITLE_CONTRACT,
    scene_title_context,
    scene_title_request_revision,
    scene_title_revision,
)
from daengs_walk.diary.relational.scene_title_writer_view import (
    SCENE_TITLE_WRITER_POLICY,
    scene_title_writer_view,
)
from daengs_walk.diary.relational.title_context import TitleAnswer


def validate_scene_titles(receipt):
    if receipt.get("title_contract") != SCENE_TITLE_CONTRACT or "title" in receipt:
        raise ValueError("invalid scene title publication marker")
    cards, titles = receipt["cards"], receipt["scene_titles"]
    ids = [card["scene_id"] for card in cards]
    if len(set(ids)) != len(ids) or set(titles) != set(ids):
        raise ValueError("scene title ownership mismatch")
    schema = TitleAnswer.model_json_schema()
    for card in cards:
        scene_id = card["scene_id"]
        title = titles[scene_id]
        context = scene_title_context(card)
        request = scene_title_writer_view(context)
        if (
            title["scene_id"] != scene_id
            or title["writer_policy"] != SCENE_TITLE_WRITER_POLICY
            or title["content_revision"] != scene_title_revision(context)
            or title["request"] != request
            or title["response_schema"] != schema
            or title["request_revision"]
            != scene_title_request_revision(title["prompt_revision"], request, schema)
        ):
            raise ValueError("scene title differs from adopted body or request")
        status = title["status"]
        if status not in {"returned", "failed", "not_requested"} or (
            (status == "not_requested") != (context is None)
        ):
            raise ValueError("scene title status differs from adopted body")
        if status == "returned":
            answer = TitleAnswer.model_validate(json.loads(title["raw_text"]))
            if title["text"] != answer.title or title["candidate"] != answer.title:
                raise ValueError("scene title differs from accepted candidate")
        elif title["text"] is not None:
            raise ValueError("unaccepted scene title cannot be published")
