"""Assemble scene requests and their frozen publication record, not prose strategy.

The persisted plan keys/version remain unchanged for v8 storage compatibility.
Preparation supplies empty delivery; execution supplies accepted prior selections.
"""

from copy import deepcopy

from daengs_walk.diary.relational.brief_contracts import ActionWritingBrief, SpaceWritingBrief
from daengs_walk.diary.relational.contracts import VERSION, writer_task
from daengs_walk.diary.relational.relations import movement, route_revisit
from daengs_walk.diary.relational.writing_brief import space_work_reason
from daengs_walk.value_contracts import digest

BRIEF_PREPARATION = "writing-brief-preparation-v1"
BRIEF_PLAN = "writing-brief-plan-v1"


def assemble_scene_requests(frame, brief: SpaceWritingBrief, previous=None):
    context = brief.context
    reason = space_work_reason(brief)
    action = (
        ActionWritingBrief.model_validate(frame["action_brief"]) if frame["action_brief"] else None
    )
    motion, revisit = movement.evaluate(frame, previous), route_revisit.evaluate(frame, previous)
    observations = sorted(motion["items"] + revisit["items"], key=lambda item: item["id"])
    plan = {
        "version": VERSION,
        "planning_contract": BRIEF_PLAN,
        "scene_id": frame["scene_id"],
        "anchor": deepcopy(frame["anchor"]),
        "state_transition": reason,
        "state_after": {"current_scene_id": frame["scene_id"]},
        "delivery_before": brief.delivery.model_dump(mode="json"),
        "relation_slots": {
            "spatial_comparison": context.relation_slots.model_dump(mode="json"),
            "movement": motion,
            "route_revisit": revisit,
        },
        "relation_selection": {
            "space": [],
            "action": [action.required_event.id] if action else [],
            "device_observations": [o["id"] for o in observations],
        },
        "standalone_context": {"facts": [f.model_dump(mode="json") for f in context.current_facts]},
        "space_task": writer_task("space", frame["scene_id"], brief)
        if reason != "unavailable"
        else None,
        "action_task": writer_task("action", frame["scene_id"], action) if action else None,
        "movement_observations": observations,
    }
    return {**plan, "revision": digest(plan)}
