"""Plans from narrative context and accepted delivery, without legacy slot admission."""

from copy import deepcopy

from daengs_walk.diary.relational.brief_contracts import ActionWritingBrief, NarrativeSpaceContext
from daengs_walk.diary.relational.contracts import VERSION, writer_task
from daengs_walk.diary.relational.relations import movement, route_revisit
from daengs_walk.diary.relational.writing_brief import build_space_brief, space_work_reason
from daengs_walk.value_contracts import digest

BRIEF_PREPARATION = "writing-brief-preparation-v1"
BRIEF_PLAN = "writing-brief-plan-v1"


def make_brief_plan(frame, previous=None, delivery=None):
    context = NarrativeSpaceContext.model_validate(frame["narrative_context"])
    brief = build_space_brief(context, delivery)
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
        if reason not in {"unavailable", "maintain"}
        else None,
        "action_task": writer_task("action", frame["scene_id"], action) if action else None,
        "movement_observations": observations,
    }
    return {**plan, "revision": digest(plan)}
