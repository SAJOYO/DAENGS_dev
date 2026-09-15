"""Delivery state is not observation state; only reviewed coverage suppresses recovery."""

from copy import deepcopy

from daengs_backend.services.walk_diary.writing.relational import (
    validate_prepared,
    write_relational_diary,
)
from daengs_walk.diary.relational.assembly import assemble_receipt
from daengs_walk.diary.relational.comparison import aware_time
from daengs_walk.diary.relational.contracts import SpaceInput, writer_task
from daengs_walk.value_contracts import digest


def context_facts(plan):
    context = plan["standalone_context"]
    values = [
        {
            "role": m["role"],
            "value": m["material"],
            "scope": m["relation"],
            "time_meaning": m.get("time_meaning"),
        }
        for m in context["space"]
    ]
    road = context.get("road_reference")
    if road:
        values.append(
            {
                "role": "road_address",
                "value": {"road_nm": road["road_nm"]},
                "scope": road["scope"],
                "time_meaning": None,
            }
        )
    return values


def context_keys(plan):
    return {m["role"]: digest(m) for m in context_facts(plan)}


def reviewed_roles(task, result):
    if not result or result.get("semantic_status") != "model_reviewed":
        return set()
    review = result.get("semantic_review", {})
    if review.get("status") != "passed":
        return set()
    refs = set(review["assessment"]["used_evidence_ids"])
    payload = task["payload"]
    roles = {m["role"] for m in payload["current_space"] if m["id"] in refs}
    road = payload.get("road_reference")
    if road and road["id"] in refs:
        roles.add("road_address")
    # A reviewed relation includes the current endpoint of this attribute.
    roles.update(r["attribute"] for r in payload["relations"] if r["id"] in refs)
    # journey alone does not prove that every current background was described.
    return roles


async def write_with_short_memory(prepared, *, enabled=True, send=None, review=True, model=None):
    if prepared["snapshot"].get("scene_comparison_version") == "scene-comparison-v1":
        from daengs_backend.services.walk_diary.writing.comparison_sequence import (
            write_comparison_sequence,
        )

        return await write_comparison_sequence(
            prepared, enabled=enabled, send=send, review=review, model=model
        )
    validate_prepared(prepared)
    snapshot = deepcopy(prepared["snapshot"])
    frames = {f["scene_id"]: f for f in snapshot["frames"]}
    times = [aware_time(p["anchor"]["event_at"]) for p in snapshot["plans"]]
    if times and (not all(times) or times != sorted(times)):
        raise ValueError("short memory requires timezone-aware chronological plans")
    recent, results, last_writing = [], [], None
    delivered_signature, covered_roles = None, set()
    for plan in snapshot["plans"]:
        observed = context_keys(plan)
        # Unknown is not a departure. It just cannot provide active narrated context.
        signature = digest(observed) if observed else None
        if not observed:
            delivered_signature, covered_roles = None, set()
        task = plan["space_task"]
        if enabled and task is None and observed and signature != delivered_signature:
            frame = frames[plan["scene_id"]]
            task = writer_task(
                "space",
                plan["scene_id"],
                SpaceInput(
                    mode="current_context",
                    current_space=plan["standalone_context"]["space"],
                    relations=(),
                    required_relation_ids=(),
                    road_reference=plan["standalone_context"].get("road_reference"),
                    narration=frame["space"].get("narration"),
                ),
            )
            plan["memory_recovery"] = "current_context_not_delivered"
        if task:
            payload = SpaceInput.model_validate(
                {**task["payload"], "short_memory": recent[-2:] if enabled else []}
            )
            plan["space_task"] = writer_task("space", plan["scene_id"], payload)
        plan["revision"] = digest({k: v for k, v in plan.items() if k != "revision"})
        partial = {**snapshot, "plans": [deepcopy(plan)]}
        last_writing = await write_relational_diary(
            {"snapshot": partial, "revision": digest(partial)},
            send=send,
            review=review,
            model=model,
        )
        results.extend(last_writing["results"])
        space = next((r for r in last_writing["results"] if r["stage"] == "space"), None)
        status = space["status"] if space else "not_requested"
        if space and space.get("status") == "returned":
            delivered_signature = signature
            covered_roles = reviewed_roles(plan["space_task"], space)
        # The context was introduced, not every optional source fact.
        plan["delivery_after"] = {
            "introduced_context_signature": delivered_signature,
            "reviewed_covered_roles": sorted(covered_roles),
            "all_context_facts_delivered": False,
        }
        plan["revision"] = digest({k: v for k, v in plan.items() if k != "revision"})
        recent.append(
            {
                "recorded_at": plan["anchor"]["event_at"],
                "confirmed_context": context_facts(plan),
                "planned_focus": plan["space_task"]["payload"]["mode"]
                if plan["space_task"]
                else None,
                "publication_status": space.get("semantic_status", status) if space else status,
                "meaning_delivery": "model_reviewed"
                if space and space.get("semantic_status") == "model_reviewed"
                else "unknown",
            }
        )
        recent = recent[-2:]
    final_prepared = {"snapshot": snapshot, "revision": digest(snapshot)}
    written = {
        **(last_writing or {}),
        "results": results,
        "snapshot_revision": final_prepared["revision"],
        "short_memory_enabled": enabled,
    }
    validate_prepared(final_prepared)
    return {"prepared": final_prepared, "receipt": assemble_receipt(final_prepared, written)}
