"""Spatial state, current actions and device observations have separate outputs."""

from copy import deepcopy

from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.relational.contracts import VERSION, ActionInput, SpaceInput, writer_task
from daengs_walk.diary.relational.relations import (
    movement_observations,
    spatial_context,
    spatial_relations,
)


def make_plan(frame, previous, catalog, state=None):
    state = deepcopy(state or {"active_context": {}, "planned_meanings": []})
    current = spatial_context(frame)
    relations = spatial_relations(frame, previous)
    changed = [r for r in relations if r["kind"] == "point_difference"]
    introduced = [r for r in relations if r["kind"] == "introduced"]
    unavailable = [r for r in relations if r["kind"] == "unconfirmed"]
    transition = (
        "introduce"
        if previous is None
        else "change"
        if changed
        else "reintroduce"
        if introduced
        else "suspend"
        if unavailable
        else "maintain"
    )
    materials = [m for role, m in current.items() if role != "road_address"]
    space_task = None
    if current and transition in {"introduce", "change", "reintroduce"}:
        mode = "initial" if previous is None else "change" if changed else "reintroduce"
        chosen = (changed or introduced)[:2]
        payload = SpaceInput(
            mode=mode,
            current_space=tuple(materials),
            relations=tuple(chosen),
            required_relation_ids=tuple(r["id"] for r in chosen) if changed else (),
            road_reference=frame.get("road_reference"),
            narration=frame["space"].get("narration"),
        )
        space_task = writer_task("space", frame["scene_id"], payload)
        for item in current.values():
            meaning = digest([item["role"], item["material"]])
            if meaning not in state["planned_meanings"]:
                state["planned_meanings"].append(meaning)
    state["active_context"] = deepcopy(current)
    action_task = None
    if frame["action"]:
        action = frame["action"]
        action_task = writer_task(
            "action",
            frame["scene_id"],
            ActionInput(
                recorded_action=action["recorded_action"],
                pin_at=frame["anchor"]["event_at"],
                current_space=tuple({**m, "id": "space:" + m["id"]} for m in materials),
                road_reference=frame.get("road_reference"),
                movement_context=action.get("movement_context"),
                narration=frame["space"].get("narration"),
            ),
        )
    plan = {
        "version": VERSION,
        "scene_id": frame["scene_id"],
        "anchor": frame.get("anchor"),
        "space_relations": relations,
        "state_transition": transition,
        "state_after": state,
        "standalone_context": {"space": materials, "road_reference": frame.get("road_reference")},
        "space_task": space_task,
        "action_task": action_task,
        "movement_observations": movement_observations(frame, previous, catalog),
    }
    return {**plan, "revision": digest(plan)}
