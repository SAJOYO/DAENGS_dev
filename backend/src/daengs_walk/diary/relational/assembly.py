"""Assemble independent parts without a final prose rewrite or action carry-over."""

from copy import deepcopy

from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.relational.contracts import VERSION


def assemble_receipt(prepared, written):
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
    for plan in prepared["snapshot"]["plans"]:
        parts = {}
        for stage in ("space", "action"):
            task = plan[stage + "_task"]
            result = results[task["id"]] if task else None
            parts[stage] = {
                "status": result["status"] if result else "not_requested",
                "text": result.get("answer", {}).get("text", "") if result else "",
                "task_id": task["id"] if task else None,
            }
        cards.append(
            {
                "scene_id": plan["scene_id"],
                "anchor": plan["anchor"],
                "parts": parts,
                "body": "\n".join(p["text"] for p in parts.values() if p["text"]),
                "standalone_context": deepcopy(plan["standalone_context"]),
                "movement_observations": deepcopy(plan["movement_observations"]),
                "originals": [
                    deepcopy(r)
                    for r in prepared["snapshot"].get("originals", [])
                    if r["scene_id"] == plan["scene_id"]
                ],
            }
        )
    return {
        "version": VERSION,
        "snapshot_revision": prepared["revision"],
        "input_revision": prepared["snapshot"]["input_revision"],
        "cards": cards,
        "writing": written,
        "delivered_task_ids": [k for k, v in results.items() if v["status"] == "returned"],
        "meaning_delivery": "not_inferred_from_reference_ids",
    }
