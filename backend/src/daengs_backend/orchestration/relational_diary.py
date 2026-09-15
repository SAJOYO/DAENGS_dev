"""One opt-in path: prepare -> delivery-aware separate writers -> review -> receipt."""

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict

from daengs_backend.services.walk_diary.relational_execution import (
    MODEL,
    RelationalConfigurationError,
    RelationalDiaryResult,
    RelationalExecutionPolicy,
)
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
    prepared,
    *,
    send=None,
    review=True,
    model=None,
    minimum_interval_s=None,
    max_calls=64,
    call_timeout_s=None,
    total_timeout_s=None,
):
    """Replay and normal preparation meet at this exact production-independent boundary.

    review=False is an explicitly marked experiment, never a semantic-success claim.
    No API/DB default is switched by this module.
    """
    if send is None:
        model = MODEL
    model = model or "injected_sender; model_not_reported"
    interval = (10.0 if send is None else 0.0) if minimum_interval_s is None else minimum_interval_s
    coordinator = CallCoordinator(
        send or generate_relation_part,
        minimum_interval_s=interval,
        max_calls=max_calls,
        call_timeout_s=call_timeout_s,
        total_timeout_s=total_timeout_s,
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
        "stopped_on_deadline": coordinator.deadline_reached,
        "call_timeout_s": call_timeout_s,
        "total_timeout_s": total_timeout_s,
        "calls": coordinator.trace,
        "automatic_retries": 0,
        "semantic_review_enabled": review,
        "sender_kind": "configured_provider" if send is None else "injected_sender",
    }
    return result


class RelationalDiaryOrchestrationService:
    """Service orchestration over our contracts, without old graph/jobs/assembly or DB writes."""

    def __init__(self, *, prepare=None, send=None, execution_policy=None):
        if prepare is None:
            from daengs_backend.services.walk_diary.collection.relational import (
                configured_relational_preparation,
            )

            prepare = configured_relational_preparation
        self.prepare = prepare
        self.send = send
        self.policy = execution_policy or RelationalExecutionPolicy()

    async def run(self, source, base, *, scene_ids=None):
        from daengs_backend.config import settings
        from daengs_walk.diary.relational.assembly import assemble_receipt

        if self.send is None and not settings.gemini_api_key.get_secret_value().strip():
            raise RelationalConfigurationError("relational model is not configured")

        revision = source.revision()
        if (
            revision != base.input.source.revision()
            or revision != base.board.input_revision
            or base.board.plan_revision != base.plan.revision()
        ):
            raise ValueError("relational writer requires its prepared source and board")
        selected = (
            tuple(scene_ids) if scene_ids is not None else tuple(s.id for s in base.board.scenes)
        )
        if len(set(selected)) != len(selected) or not set(selected) <= {
            s.id for s in base.board.scenes
        }:
            raise ValueError("invalid relational scene selection")
        frozen = deepcopy(base)
        board_revision = frozen.board.plan_revision
        async with asyncio.timeout(self.policy.preparation_timeout_s):
            prepared = await self.prepare(frozen, scene_ids=selected)
        prepared = deepcopy(prepared)
        snapshot = prepared["snapshot"]
        if (
            snapshot["input_revision"] != revision
            or snapshot["board_revision"] != board_revision
            or snapshot.get("scene_comparison_version") != "scene-comparison-v1"
            or {f["scene_id"] for f in snapshot["frames"]} != set(selected)
            or {p["scene_id"] for p in snapshot["plans"]} != set(selected)
            or any(
                f.get("planning_contract") != "scene-comparison-plan-v1" for f in snapshot["frames"]
            )
        ):
            raise ValueError("collector returned a different relational preparation")
        # The writing entrypoint isolates and validates the complete sources
        # before its first provider call; do not repeat that reconstruction here.
        action_count = sum(bool(p["action_task"]) for p in snapshot["plans"])
        policy = self.policy.resolve(len(snapshot["frames"]), action_count)
        call_limit = policy.call_budget(len(snapshot["frames"]), action_count)
        generated = await generate_prepared_relational_diary(
            prepared,
            send=self.send,
            review=policy.semantic_review,
            model=MODEL if self.send is None else "injected_sender; model_not_reported",
            minimum_interval_s=policy.minimum_interval_s,
            max_calls=call_limit,
            call_timeout_s=policy.call_timeout_s,
            total_timeout_s=policy.generation_timeout_s,
        )
        # Keep actual accepted results, including failures and intentional omissions.
        expected = assemble_receipt(generated["prepared"], generated["receipt"]["writing"])
        if any(generated["receipt"].get(k) != v for k, v in expected.items()):
            raise ValueError("relational receipt changed after writing")
        generated["receipt"]["execution"]["policy"] = asdict(policy)
        return RelationalDiaryResult(
            input_revision=revision,
            board_revision=board_revision,
            prepared=generated["prepared"],
            receipt=generated["receipt"],
        )


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
