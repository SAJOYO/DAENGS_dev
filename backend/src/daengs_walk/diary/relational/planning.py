"""Spatial state, current actions and device observations have separate outputs."""

from copy import deepcopy

from daengs_walk.diary.relational.comparison import writing_relation
from daengs_walk.diary.relational.contracts import VERSION, ActionInput, SpaceInput, writer_task
from daengs_walk.diary.relational.journey import writing_journey
from daengs_walk.diary.relational.relations import (
    movement_observations,
    spatial_context,
)
from daengs_walk.diary.relational.relations.registry import collect_relations
from daengs_walk.value_contracts import digest


def make_plan(frame, previous, catalog, state=None):
    state = deepcopy(state or {"active_context": {}, "planned_meanings": []})
    current = spatial_context(frame)
    frame = deepcopy(frame)
    if "relation_observations" not in frame:
        comparable = (
            previous is not None
            and catalog is not None
            and frame.get("block") is not None
            and frame["block"] == previous.get("block")
        )
        frame["relation_observations"] = (
            movement_observations(frame, previous, catalog) if comparable else None
        )
    relation_slots = collect_relations(frame, previous)
    relations = relation_slots["background"]["items"]
    changed = [
        r
        for r in relations
        if r["kind"] in {"location_contrast", "record_context_contrast", "temporal_contrast"}
    ]
    introduced = [r for r in relations if r["kind"] == "introduced"]
    unavailable = [r for r in relations if r["kind"] == "unconfirmed"]
    deferred = [r for r in relations if r["kind"] == "deferred"]
    transition = (
        "introduce"
        if previous is None
        else "change"
        if changed
        else "reintroduce"
        if introduced
        else "suspend"
        if unavailable
        else "refresh_context"
        if deferred
        else "maintain"
    )
    materials = [m for role, m in current.items() if role != "road_address"]
    if "scene_snapshot" in frame:
        materials = [m for m in materials if m["role"] != "location_label"]
    space_task = None
    if current and transition in {"introduce", "change", "reintroduce", "refresh_context"}:
        journey = frame.get("journey")
        mode = "express_relation" if changed else "current_context"
        if journey and journey["status"] == "connected" and journey["moving_distance_m"] > 0:
            mode = "spatial_journey"
        chosen = changed  # At most the supported roles; do not drop a third changed role.
        payload = SpaceInput(
            mode=mode,
            journey=writing_journey(journey, previous, frame) if journey else None,
            current_space=tuple(materials),
            relations=tuple(writing_relation(r) for r in chosen),
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
        action = relation_slots["event_context"]["items"][0]
        action_task = writer_task(
            "action",
            frame["scene_id"],
            ActionInput(
                recorded_action=action["recorded_action"],
                pin_at=frame["anchor"]["event_at"],
                current_space=tuple({**m, "id": "space:" + m["id"]} for m in materials),
                road_reference=frame.get("road_reference"),
                movement_context=action.get("movement_context"),
                current_gait=action.get('current_gait', ()),
                current_shape=action.get('current_shape', ()),
                narration=frame["space"].get("narration"),
            ),
        )
    plan = {
        "version": VERSION,
        "scene_id": frame["scene_id"],
        "anchor": frame.get("anchor"),
        "space_relations": relations,
        "relation_slots": relation_slots,
        "relation_selection": {
            "space": [r["id"] for r in changed],
            "action": [r["id"] for r in relation_slots["event_context"]["items"]],
            "device_observations": [
                r["id"]
                for name in ("movement", "route_revisit")
                for r in relation_slots[name]["items"]
            ],
        },
        "journey": deepcopy(frame.get("journey")),
        "state_transition": transition,
        "state_after": state,
        "standalone_context": {"space": materials, "road_reference": frame.get("road_reference")},
        "space_task": space_task,
        "action_task": action_task,
        "movement_observations": sorted(
            relation_slots["movement"]["items"] + relation_slots["route_revisit"]["items"],
            key=lambda o: o["id"],
        ),
    }
    if "scene_snapshot" in frame:
        from daengs_walk.diary.relational.comparison_writing import (
            comparison_input,
            should_write_space,
        )

        plan["space_task"] = (
            writer_task("space", frame["scene_id"], comparison_input(frame, previous))
            if should_write_space(frame, previous) else None
        )
        plan["state_transition"] = (
            "introduce" if previous is None else "compare" if plan["space_task"] else "maintain"
        )
        plan["relation_selection"]["space"] = []  # Selection is returned by the writer.
    return {**plan, "revision": digest(plan)}
