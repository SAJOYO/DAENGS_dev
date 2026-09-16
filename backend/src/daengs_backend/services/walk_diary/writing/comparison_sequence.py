"""Execute snapshot tasks in scene order; recover an undelivered introduction."""

from copy import deepcopy

from daengs_walk.diary.relational.assembly import assemble_receipt
from daengs_walk.diary.relational.comparison_writing import comparison_input
from daengs_walk.diary.relational.contracts import writer_task
from daengs_walk.diary.relational.delivery import (
    DELIVERY_POLICY,
    DeliveryState,
    advance_delivery,
    context_signature,
)
from daengs_walk.value_contracts import digest


async def write_comparison_sequence(prepared, *, enabled, send, review, model):
    from daengs_backend.services.walk_diary.writing.relational import (
        _validate_plans,
        _write_validated_tasks,
        validate_prepared,
    )

    # Validate an isolated copy before the first await. Neither callers nor model
    # senders receive this owned source bundle; only copied task payloads leave it.
    frozen = deepcopy(prepared)
    validate_prepared(frozen)
    snapshot = frozen["snapshot"]
    snapshot["delivery_policy"] = DELIVERY_POLICY
    frames = snapshot["frames"]
    positions = {f["scene_id"]: i for i, f in enumerate(frames)}
    results, memory, last = [], DeliveryState(), {}
    for plan in snapshot["plans"]:
        index = positions[plan["scene_id"]]
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
        tasks = _validate_plans(snapshot, [plan], frame_positions=positions)
        last = await _write_validated_tasks(
            tasks,
            snapshot_revision=frozen["revision"],
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
    _validate_plans(snapshot, frame_positions=positions)
    return {"prepared": final, "receipt": assemble_receipt(final, written)}
