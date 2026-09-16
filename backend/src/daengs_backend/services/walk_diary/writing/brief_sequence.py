"""Sequential spatial delivery; current dog events share only the call coordinator."""

from copy import deepcopy

from daengs_backend.services.walk_diary.writing.brief_prompts import BRIEF_POLICY, BRIEF_PROMPTS
from daengs_walk.diary.relational.brief_contracts import (
    BriefDeliveryState,
    DeliveredMeaning,
    SpaceWritingBrief,
)
from daengs_walk.diary.relational.scene_requests import assemble_scene_requests
from daengs_walk.diary.relational.writing_brief import advance_brief_delivery
from daengs_walk.value_contracts import digest


async def write_brief_sequence(prepared, *, enabled=True, send=None, review=False, model=None):
    from daengs_backend.services.walk_diary.writing.relational import (
        _validate_plans,
        _write_validated_tasks,
        validate_prepared,
    )
    from daengs_walk.diary.relational.assembly import assemble_receipt

    frozen = deepcopy(prepared)
    validate_prepared(
        frozen
    )  # Complete source reconstruction happens once, before the first await.
    snapshot = frozen["snapshot"]
    frames, plans, results = snapshot["frames"], [], []
    positions = {f["scene_id"]: i for i, f in enumerate(frames)}
    memory, last = BriefDeliveryState(), {}
    for index, frame in enumerate(frames):
        brief = SpaceWritingBrief(
            context=frame["narrative_context"],
            delivery=memory if enabled else BriefDeliveryState(),
        )
        plan = assemble_scene_requests(frame, brief, frames[index - 1] if index else None)
        tasks = _validate_plans(snapshot, [plan], frame_positions=positions)
        last = await _write_validated_tasks(
            tasks, snapshot_revision=frozen["revision"], send=send, review=review, model=model
        )
        results.extend(last["results"])
        space = next((r for r in last["results"] if r["stage"] == "space"), None)
        selection = None
        if space and space["status"] == "returned":
            selection = DeliveredMeaning(
                context=brief.context,
                evidence_ids=space["answer"]["evidence_ids"],
                relation_ids=space["answer"]["relation_ids"],
                semantic_status=space["semantic_status"],
            )
        memory = advance_brief_delivery(brief, selection)
        plan["delivery_after"] = memory.model_dump(mode="json")
        plan["revision"] = digest({k: v for k, v in plan.items() if k != "revision"})
        plans.append(plan)
    snapshot["plans"] = plans
    final = {"snapshot": snapshot, "revision": digest(snapshot)}
    written = {
        **last,
        "results": results,
        "snapshot_revision": final["revision"],
        "policy": BRIEF_POLICY,
        "prompt_revision": digest(BRIEF_PROMPTS),
        "short_memory_enabled": enabled,
        "memory_strategy": "accepted_narrative_selections",
        "semantic_validation": "model_review_not_proof" if review else "not_performed",
    }
    return {"prepared": final, "receipt": assemble_receipt(final, written)}
