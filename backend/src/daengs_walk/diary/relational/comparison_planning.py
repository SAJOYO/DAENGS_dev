"""Direct snapshot planning; no legacy space writer or background comparison plan."""

from copy import deepcopy

from daengs_walk.diary.relational.comparison_writing import comparison_input, should_write_space
from daengs_walk.diary.relational.contracts import VERSION, ActionInput, writer_task
from daengs_walk.diary.relational.current_action import current_background
from daengs_walk.diary.relational.relations import movement, route_revisit
from daengs_walk.value_contracts import digest


def make_comparison_plan(frame, previous):
    materials = current_background(frame["scene_snapshot"])
    action = frame.get("action")
    action_task = None
    if action:
        action_task = writer_task(
            "action",
            frame["scene_id"],
            ActionInput(
                recorded_action=action["recorded_action"],
                pin_at=frame["anchor"]["event_at"],
                current_space=tuple({**m, "id": "space:" + m["id"]} for m in materials),
                road_reference=frame.get("road_reference"),
                current_gait=action.get("current_gait", ()),
                current_shape=action.get("current_shape", ()),
                narration=frame["space"].get("narration"),
            ),
        )
    space_task = (
        writer_task("space", frame["scene_id"], comparison_input(frame, previous))
        if should_write_space(frame, previous)
        else None
    )
    motion = movement.evaluate(frame, previous)
    revisit = route_revisit.evaluate(frame, previous)
    observations = sorted(motion["items"] + revisit["items"], key=lambda item: item["id"])
    plan = {
        "version": VERSION,
        "planning_contract": "scene-comparison-plan-v1",
        "scene_id": frame["scene_id"],
        "anchor": deepcopy(frame["anchor"]),
        "space_relations": [],
        "relation_slots": {
            "spatial_comparison": deepcopy(frame["spatial_comparison_slots"]),
            "movement": motion,
            "route_revisit": revisit,
        },
        "relation_selection": {
            "space": [],
            "action": [action["recorded_action"]["id"]] if action else [],
            "device_observations": [item["id"] for item in observations],
        },
        "journey": deepcopy(frame.get("journey")),
        "state_transition": "introduce"
        if previous is None
        else "compare"
        if space_task
        else "maintain",
        # Actual delivery state is advanced only after accepted writing, not here.
        "state_after": {"current_scene_id": frame["scene_id"]},
        "standalone_context": {"space": materials, "road_reference": frame.get("road_reference")},
        "space_task": space_task,
        "action_task": action_task,
        "movement_observations": observations,
    }
    return {**plan, "revision": digest(plan)}
