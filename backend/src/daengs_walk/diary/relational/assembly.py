"""Assemble independent parts without a final prose rewrite or action carry-over."""

from copy import deepcopy

from daengs_walk.diary.relational.comparison_writing import comparison_input
from daengs_walk.diary.relational.contracts import VERSION
from daengs_walk.diary.relational.delivery import (
    LEGACY_DELIVERY_POLICY,
    DeliveryState,
    advance_delivery,
)
from daengs_walk.diary.relational.publication import (
    PUBLICATION_VERSION,
    ComparisonPublication,
    validate_publication,
)
from daengs_walk.value_contracts import digest


def assemble_receipt(prepared, written):
    if prepared["snapshot"].get("writing_brief_version"):
        from daengs_walk.diary.relational.brief_assembly import assemble_brief_receipt

        return assemble_brief_receipt(prepared, written)
    if (
        digest(prepared["snapshot"]) != prepared["revision"]
        or written["snapshot_revision"] != prepared["revision"]
    ):
        raise ValueError("writing belongs to a different snapshot")
    results = {r["task_id"]: r for r in written["results"]}
    tasks = {
        p[k]["id"]: p[k]
        for p in prepared["snapshot"]["plans"]
        for k in ("space_task", "action_task")
        if p[k]
    }
    if len(results) != len(written["results"]) or results.keys() != tasks.keys():
        raise ValueError("missing, duplicate or unexpected task result")
    for key, value in results.items():
        task = tasks[key]
        if any(value[k] != task[k] for k in ("scene_id", "stage", "revision")):
            raise ValueError("result task binding changed")
    cards = []
    comparison = prepared["snapshot"].get("scene_comparison_version") == "scene-comparison-v1"
    frames = prepared["snapshot"]["frames"]
    memory = DeliveryState()
    for plan in prepared["snapshot"]["plans"]:
        parts = {}
        for stage in ("space", "action"):
            task = plan[stage + "_task"]
            result = results[task["id"]] if task else None
            parts[stage] = {
                "status": result["status"] if result else "not_requested",
                "text": result["answer"]["text"]
                if result and result["status"] == "returned"
                else "",
                "task_id": task["id"] if task else None,
            }
        cards.append(
            {
                "scene_id": plan["scene_id"],
                "anchor": plan["anchor"],
                "parts": parts,
                "body": "\n".join(p["text"] for p in parts.values() if p["text"]),
                "standalone_context": deepcopy(plan["standalone_context"]),
                "relation_slots": deepcopy(plan["relation_slots"]),
                "relation_selection": deepcopy(plan["relation_selection"]),
                "movement_observations": deepcopy(plan["movement_observations"]),
                "originals": [
                    deepcopy(r)
                    for r in prepared["snapshot"].get("originals", [])
                    if r["scene_id"] == plan["scene_id"]
                ],
            }
        )
        if comparison:
            index = next(
                i for i, frame in enumerate(frames) if frame["scene_id"] == plan["scene_id"]
            )
            frame = frames[index]
            context = comparison_input(frame, frames[index - 1] if index else None)
            task = plan["space_task"]
            result = results[task["id"]] if task else None
            selected = result.get("answer") if result and result["status"] == "returned" else None
            after = advance_delivery(
                memory,
                frame,
                task,
                result,
                policy=prepared["snapshot"].get("delivery_policy", LEGACY_DELIVERY_POLICY),
            )
            for key, state in (("delivery_before", memory), ("delivery_after", after)):
                if key in plan and plan[key] != state.model_dump(mode="json"):
                    raise ValueError("planned delivery differs from accepted results")
            publication = ComparisonPublication(
                context=context,
                context_revision=digest(context),
                header=frame["card_header"],
                selection=selected,
                semantic_status=result["semantic_status"] if selected else "not_published",
                delivery_before=memory,
                delivery_after=after,
            )
            cards[-1]["comparison"] = publication.model_dump(mode="json")
            cards[-1]["relation_selection"]["space"] = selected["relation_ids"] if selected else []
            memory = after
    receipt = {
        "version": PUBLICATION_VERSION if comparison else VERSION,
        "snapshot_revision": prepared["revision"],
        "input_revision": prepared["snapshot"]["input_revision"],
        "cards": cards,
        "writing": written,
        "delivered_task_ids": [k for k, v in results.items() if v["status"] == "returned"],
        "meaning_delivery": "not_inferred_from_reference_ids",
    }
    if comparison:
        if "delivery_policy" in prepared["snapshot"]:
            receipt["delivery_policy"] = prepared["snapshot"]["delivery_policy"]
        validate_publication(receipt)
    return receipt
