"""Freeze new brief results without assigning new narrative IDs old v7 meanings.

Persistence/read compatibility is a separate adapter. This assembles the service result.
"""

from copy import deepcopy

from daengs_walk.diary.relational.brief_binding import validate_brief_plans
from daengs_walk.diary.relational.brief_contracts import BriefDeliveryState, DeliveredMeaning
from daengs_walk.diary.relational.brief_publication import BRIEF_PUBLICATION, validate_brief_result
from daengs_walk.diary.relational.comparison_writing import comparison_input
from daengs_walk.diary.relational.writing_brief import (
    advance_brief_delivery,
    build_space_brief,
)
from daengs_walk.value_contracts import digest


def assemble_brief_receipt(prepared, written):
    snapshot = prepared["snapshot"]
    if (
        digest(snapshot) != prepared["revision"]
        or written["snapshot_revision"] != prepared["revision"]
    ):
        raise ValueError("brief writing belongs to a different snapshot")
    tasks = {t.id: t for t in validate_brief_plans(snapshot)}
    results = {r["task_id"]: r for r in written["results"]}
    if len(results) != len(written["results"]) or results.keys() != tasks.keys():
        raise ValueError("missing, duplicate or unexpected brief result")
    for key, result in results.items():
        validate_brief_result(tasks[key], result)
    cards, memory = [], BriefDeliveryState()
    frames = snapshot["frames"]
    for index, (frame, plan) in enumerate(zip(frames, snapshot["plans"], strict=True)):
        if frame["scene_id"] != plan["scene_id"]:
            raise ValueError("published frame order differs from plans")
        before = memory if written.get("short_memory_enabled", True) else BriefDeliveryState()
        if plan["delivery_before"] != before.model_dump(mode="json"):
            raise ValueError("brief memory differs from accepted earlier results")
        brief = build_space_brief(frame["narrative_context"], before)
        parts, selected = {}, None
        for stage in ("space", "action"):
            task = plan[stage + "_task"]
            result = results[task["id"]] if task else None
            answer = result["answer"] if result and result["status"] == "returned" else None
            parts[stage] = {
                "status": result["status"] if result else "not_requested",
                "text": answer["text"] if answer else "",
                "task_id": task["id"] if task else None,
            }
            if stage == "space" and answer:
                selected = DeliveredMeaning(
                    context=brief.context,
                    evidence_ids=answer["evidence_ids"],
                    relation_ids=answer["relation_ids"],
                    semantic_status=result["semantic_status"],
                )
        memory = advance_brief_delivery(brief, selected)
        if plan["delivery_after"] != memory.model_dump(mode="json"):
            raise ValueError("brief memory after publication changed")
        relation_selection = deepcopy(plan["relation_selection"])
        relation_selection["space"] = list(selected.relation_ids) if selected else []
        cards.append(
            {
                "scene_id": frame["scene_id"],
                "anchor": deepcopy(frame["anchor"]),
                "parts": parts,
                "body": "\n".join(p["text"] for p in parts.values() if p["text"]),
                "standalone_context": deepcopy(plan["standalone_context"]),
                "relation_slots": deepcopy(plan["relation_slots"]),
                "relation_selection": relation_selection,
                "movement_observations": deepcopy(plan["movement_observations"]),
                "originals": [
                    deepcopy(o) for o in snapshot["originals"] if o["scene_id"] == frame["scene_id"]
                ],
                "comparison": {
                    "version": "brief-comparison-publication-v1",
                    "context": comparison_input(
                        frame, frames[index - 1] if index else None
                    ).model_dump(mode="json"),
                    "header": deepcopy(frame["card_header"]),
                },
                "space_brief": brief.model_dump(mode="json"),
                "action_brief": deepcopy(frame["action_brief"]),
                "delivery_after": memory.model_dump(mode="json"),
            }
        )
    return {
        "version": BRIEF_PUBLICATION,
        "snapshot_revision": prepared["revision"],
        "input_revision": snapshot["input_revision"],
        "cards": cards,
        "writing": written,
        "delivered_task_ids": [k for k, r in results.items() if r["status"] == "returned"],
        "meaning_delivery": "not_inferred_from_reference_ids",
    }
