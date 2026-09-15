"""Reconstruct brief facts from frozen sources once; validate small plans separately."""

from daengs_walk.diary.contracts.input import UserRecord
from daengs_walk.diary.relational.brief_contracts import BriefDeliveryState
from daengs_walk.diary.relational.brief_planning import (
    BRIEF_PLAN,
    BRIEF_PREPARATION,
    make_brief_plan,
)
from daengs_walk.diary.relational.comparison import aware_time
from daengs_walk.diary.relational.comparison_writing import comparison_input
from daengs_walk.diary.relational.contracts import WriterTask
from daengs_walk.diary.relational.current_action import (
    build_current_event_brief,
    motion_from_evidence,
)
from daengs_walk.diary.relational.narrative_space import build_space_context
from daengs_walk.diary.relational.walk_phase import ScenePosition
from daengs_walk.value_contracts import digest


def validate_brief_sources(snapshot):
    if snapshot.get("writing_brief_version") != BRIEF_PREPARATION:
        raise ValueError("unsupported writing brief preparation")
    frames = snapshot["frames"]
    positions = {
        key: ScenePosition.model_validate(value)
        for key, value in snapshot["scene_positions"].items()
    }
    if set(positions) != {f["scene_id"] for f in frames}:
        raise ValueError("brief positions differ from selected frames")
    subjects = snapshot["event_subjects"]
    if len(set(subjects["pet_ids"])) != len(subjects["pet_ids"]):
        raise ValueError("duplicate event subjects")
    previous = None
    for index, frame in enumerate(frames, 1):
        position = positions[frame["scene_id"]]
        if (
            position.scene_id != frame["scene_id"]
            or position.selected_scene_number != index
            or position.selected_scene_count != len(frames)
            or position.timeline.source_revision != snapshot["input_revision"]
            or position.recorded_at != aware_time(frame["anchor"]["event_at"])
        ):
            raise ValueError("brief chronology differs from frozen frames")
        context = build_space_context(comparison_input(frame, previous), positions)
        if frame["narrative_context"] != context.model_dump(mode="json"):
            raise ValueError("narrative context differs from source facts")
        saved_record = frame["behavior_record"]
        record = UserRecord.model_validate(saved_record) if saved_record else None
        if record and record.anchor.model_dump(mode="json") != frame["anchor"]:
            raise ValueError("behavior pin anchor differs from frame")
        action = build_current_event_brief(
            record,
            context,
            subjects["pet_ids"],
            subjects["pet_names"],
            motion_from_evidence(frame["eligible_evidence"]),
        )
        if frame["action_brief"] != (action.model_dump(mode="json") if action else None) or bool(
            frame["action"]
        ) != bool(action):
            raise ValueError("action brief differs from current source pin")
        previous = frame


def validate_brief_plans(snapshot, plans=None, *, frame_positions=None):
    plans = snapshot["plans"] if plans is None else plans
    positions = frame_positions or {f["scene_id"]: i for i, f in enumerate(snapshot["frames"])}
    order = [positions[p["scene_id"]] for p in plans]
    if order != sorted(set(order)):
        raise ValueError("duplicate or unordered brief plans")
    tasks = []
    for plan in plans:
        index = positions[plan["scene_id"]]
        frame = snapshot["frames"][index]
        previous = snapshot["frames"][index - 1] if index else None
        if (
            frame["planning_contract"] != BRIEF_PLAN
            or digest({k: v for k, v in plan.items() if k != "revision"}) != plan["revision"]
        ):
            raise ValueError("brief plan changed")
        state = BriefDeliveryState.model_validate(plan["delivery_before"])
        expected = make_brief_plan(frame, previous, state)
        supplied = {k: v for k, v in plan.items() if k not in {"revision", "delivery_after"}}
        if supplied != {k: v for k, v in expected.items() if k != "revision"}:
            raise ValueError("brief plan does not match its source context and delivery")
        tasks.extend(
            WriterTask.model_validate(plan[k]) for k in ("space_task", "action_task") if plan[k]
        )
    return tasks
