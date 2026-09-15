"""Execute snapshot tasks in scene order; recover an undelivered introduction."""

from copy import deepcopy

from daengs_walk.diary.relational.assembly import assemble_receipt
from daengs_walk.diary.relational.comparison_writing import comparison_input
from daengs_walk.diary.relational.contracts import writer_task
from daengs_walk.diary.relational.delivery import DeliveryState, advance_delivery, context_signature
from daengs_walk.value_contracts import digest


async def write_comparison_sequence(prepared, *, enabled, send, review, model):
    from daengs_backend.services.walk_diary.writing.relational import (
        validate_prepared,
        write_relational_diary,
    )

    snapshot = deepcopy(prepared["snapshot"])
    frames = snapshot["frames"]
    results, memory, last = [], DeliveryState(), {}
    for plan in snapshot["plans"]:
        index = next(i for i, f in enumerate(frames) if f["scene_id"] == plan["scene_id"])
        frame, previous = frames[index], frames[index - 1] if index else None
        signature = context_signature(frame)
        delivered = memory.active_introduction
        delivered = delivered.context_signature if delivered else None
        plan["delivery_before"] = memory.model_dump(mode="json")
        if enabled and plan["space_task"] is None and signature and signature != delivered:
            plan["space_task"] = writer_task(
                "space", frame["scene_id"], comparison_input(frame, previous)
            )
            plan["memory_recovery"] = "current_context_not_delivered"
        plan["revision"] = digest({k: v for k, v in plan.items() if k != "revision"})
        partial = {**snapshot, "plans": [deepcopy(plan)]}
        last = await write_relational_diary(
            {"snapshot": partial, "revision": digest(partial)},
            send=send,
            review=review,
            model=model,
        )
        results.extend(last["results"])
        space = next((r for r in last["results"] if r["stage"] == "space"), None)
        memory = advance_delivery(memory, frame, plan["space_task"], space)
        plan["delivery_after"] = memory.model_dump(mode="json")
        plan["revision"] = digest({k: v for k, v in plan.items() if k != "revision"})
    final = {"snapshot": snapshot, "revision": digest(snapshot)}
    written = {
        **last,
        "results": results,
        "snapshot_revision": final["revision"],
        "short_memory_enabled": enabled,
        "memory_strategy": "adjacent_source_snapshots_with_intro_recovery",
    }
    validate_prepared(final)
    return {"prepared": final, "receipt": assemble_receipt(final, written)}
