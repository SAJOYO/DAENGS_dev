"""Current pin attachment only. An event never persists into another frame."""

from copy import deepcopy

from .background import spatial_context
from .contracts import slot


def evaluate(frame, previous):
    action = frame.get("action")
    if not action:
        return slot("not_applicable", "No current action pin")
    facts = list(spatial_context(frame).values())
    item = {
        "id": "event-context:" + frame["scene_id"],
        "kind": "current_pin_context",
        "pin_at": frame["anchor"]["event_at"],
        "recorded_action": deepcopy(action["recorded_action"]),
        "current_space": deepcopy(facts),
        "movement_context": deepcopy(action.get("movement_context")),
        **{
            key: deepcopy(action[key]) for key in ("current_gait", "current_shape") if key in action
        },
        "source_ids": [frame["scene_id"] + ":" + action["recorded_action"]["id"]]
        + [frame["scene_id"] + ":" + m["id"] for m in facts],
        "scope": "Current pin and its attached context only; device movement is not action duration or cause",
    }
    return slot("confirmed", "Current recorded event; not a carried state", [item])
