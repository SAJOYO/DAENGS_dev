"""One opt-in path: prepare -> delivery-aware separate writers -> review -> receipt."""

import json
from copy import deepcopy

from daengs_backend.services.walk_diary.writing.relational import (
    FAILURES,
    PROMPTS,
    failure_record,
    generate_relation_part,
    review_answer,
)
from daengs_backend.services.walk_diary.writing.relational_transport import CallCoordinator
from daengs_backend.services.walk_diary.writing.short_memory import write_with_short_memory
from daengs_walk.value_contracts import digest


async def generate_prepared_relational_diary(
    prepared, *, send=None, review=True, model=None, minimum_interval_s=None, max_calls=64
):
    """Replay and normal preparation meet at this exact production-independent boundary.

    review=False is an explicitly marked experiment, never a semantic-success claim.
    No API/DB default is switched by this module.
    """
    if send is None:
        from daengs_backend.services.walk_diary.writing import policy

        model = policy.MODEL
    model = model or "injected_sender; model_not_reported"
    interval = (10.0 if send is None else 0.0) if minimum_interval_s is None else minimum_interval_s
    coordinator = CallCoordinator(
        send or generate_relation_part, minimum_interval_s=interval, max_calls=max_calls
    )
    result = await write_with_short_memory(prepared, send=coordinator, review=review, model=model)
    receipt = result["receipt"]
    scenes = []
    for card in receipt["cards"]:
        if card["body"]:
            scenes.append({"id": f"scene:{len(scenes) + 1}", "body": card["body"]})
        for observation in card["movement_observations"]:
            scenes.append(
                {"id": f"scene:{len(scenes) + 1}", "device_observation": observation["text"]}
            )
    title = {"status": "not_requested", "text": "산책 기록"}
    if scenes:
        phase = "request"
        try:
            request = {"scenes": scenes}
            schema = {
                "type": "object",
                "additionalProperties": False,
                "properties": {"title": {"type": "string", "minLength": 1, "maxLength": 30}},
                "required": ["title"],
            }
            title["request"] = deepcopy(request)
            title["request_revision"] = digest([PROMPTS["title"], request, schema])
            raw = await coordinator("title", deepcopy(request), deepcopy(schema))
            title["raw_text"] = raw
            value = json.loads(raw)["title"]
            if not isinstance(value, str) or not value.strip() or len(value) > 30:
                raise ValueError("invalid title")
            title["candidate"] = value
            if review:
                phase = "semantic_review"
                title["semantic_review"] = {}
                checked = await review_answer(
                    "title",
                    request,
                    {"text": value, "evidence_ids": [s["id"] for s in scenes]},
                    set(),
                    coordinator,
                    audit=title["semantic_review"],
                )
                title["semantic_review"] = checked
                if checked["status"] != "passed":
                    raise ValueError("title semantic review rejected")
            title.update(
                status="returned",
                text=value,
                semantic_status="model_reviewed" if review else "unverified",
            )
        except FAILURES as exc:
            failure_record(title, exc, phase)
    receipt["title"] = title
    receipt["execution"] = {
        "model": model,
        "model_call_attempts": coordinator.calls,
        "max_model_calls": max_calls,
        "minimum_interval_s": interval,
        "stopped_on_rate_limit": coordinator.stopped,
        "calls": coordinator.trace,
        "automatic_retries": 0,
        "semantic_review_enabled": review,
        "sender_kind": "configured_provider" if send is None else "injected_sender",
    }
    return result


async def generate_relational_skeleton(
    base,
    *,
    scene_ids=None,
    road_snapshots=(),
    send=None,
    review=True,
    model=None,
    minimum_interval_s=None,
    max_calls=64,
):
    from daengs_backend.services.walk_diary.preparation.relational import prepare_relational_diary

    prepared = prepare_relational_diary(base, scene_ids=scene_ids, road_snapshots=road_snapshots)
    return await generate_prepared_relational_diary(
        prepared,
        send=send,
        review=review,
        model=model,
        minimum_interval_s=minimum_interval_s,
        max_calls=max_calls,
    )
