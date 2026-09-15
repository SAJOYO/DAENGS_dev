"""Prepare an opt-in relation strategy using real board sources, without persistence."""

from copy import deepcopy

from daengs_backend.services.walk_diary.preparation.scene_snapshot import assemble_scene_snapshot
from daengs_walk.diary.board.models import VerifiedBoardRoute
from daengs_walk.diary.relational.current_action import current_action, current_background
from daengs_walk.diary.relational.journey import extract_journey
from daengs_walk.diary.relational.planning import VERSION, make_plan
from daengs_walk.diary.relational.relations import movement_observations
from daengs_walk.diary.relational.relations.registry import collect_spatial_comparisons
from daengs_walk.diary.route.binding import verified_route
from daengs_walk.diary.route.movement import prepare_movement
from daengs_walk.diary.slots.service import prepare_eligible_scene_facts
from daengs_walk.value_contracts import digest


def prepare_relational_diary(base, *, scene_ids=None, road_snapshots=()):
    """Keep full eligible facts; selected scenes define the comparison intervals."""
    road_snapshots = tuple(road_snapshots)
    observation = base.input.observation_source
    route = (
        VerifiedBoardRoute(observation.route, observation.evidence)
        if observation is not None and observation.evidence is not None
        else None
    )
    _, _, route_revision = verified_route(base.input.source, route)
    eligible = prepare_eligible_scene_facts(
        base.input.source,
        base.board,
        base.slots.policy,
        route=route,
        scene_backgrounds=base.scene_backgrounds,
    )
    policy = base.slots.policy
    catalog = (
        prepare_movement(base.input.source, route, policy.movement, policy.route_patterns)
        if policy.movement is not None
        else None
    )
    selected = set(scene_ids) if scene_ids is not None else {s.id for s in base.board.scenes}
    if not selected <= {s.id for s in base.board.scenes}:
        raise ValueError("unknown relation scene")
    frames, plans, originals = [], [], []
    scene_backgrounds = {}
    state = None
    for scene in sorted(base.board.scenes, key=lambda s: (s.anchor.event_at, s.id)):
        if scene.id not in selected:
            continue
        action_data = current_action(
            scene, base.input.source, base.input.pet_names, eligible[scene.id]
        )
        narration = {
            "narrator": "이 산책을 기록한 보호자(나)",
            "companions": [
                {"name": dict(base.input.pet_names).get(p)} for p in base.input.source.pet_ids
            ],
            "scope": "현재 장면",
        }
        motion = [e for e in eligible[scene.id] if e.role == "scene_movement"]
        blocks = {c["block"] for e in motion for c in e.facts["claims"]}
        frame = {
            "walk_session": base.input.source.revision(),
            "scene_id": scene.id,
            "anchor": scene.anchor.model_dump(mode="json"),
            "at_s": (scene.anchor.event_at - base.input.source.started_at).total_seconds(),
            "block": next(iter(blocks)) if len(blocks) == 1 else None,
            "space": {"narration": narration, "materials": []},
            "planning_contract": "scene-comparison-plan-v1",
            "action": action_data,
            "eligible_evidence": [e.model_dump(mode="json") for e in eligible[scene.id]],
        }
        background_sources = [
            b.model_dump(mode="json")
            for b in (
                *base.input.source.backgrounds,
                *(base.scene_backgrounds.backgrounds if base.scene_backgrounds else ()),
            )
            if b.target == scene.core_ref
        ]
        for background in background_sources:
            old = scene_backgrounds.setdefault(background["id"], background)
            if old != background:
                raise ValueError("conflicting saved background versions")
        frame["scene_background_ids"] = sorted({b["id"] for b in background_sources})
        scene_snapshot, card_header = assemble_scene_snapshot(
            frame, backgrounds=background_sources, road_snapshots=road_snapshots
        )
        frame["scene_snapshot"] = scene_snapshot.model_dump(mode="json")
        frame["card_header"] = card_header.model_dump(mode="json")
        # Current action receives only the current point background, never prior relations.
        frame["space"]["materials"] = current_background(frame["scene_snapshot"])
        road = next((f for f in scene_snapshot.facts if f.family == "road"), None)
        if road:
            frame["road_reference"] = {
                "id": road.id,
                "road_nm": road.value["name"],
                "scope": road.scope.description,
            }
        else:
            frame.pop("road_reference", None)
        frame["spatial_comparison_slots"] = collect_spatial_comparisons(
            frame["scene_snapshot"], frames[-1]["scene_snapshot"] if frames else None
        )
        record = getattr(scene.core, "record", None)
        if record is not None and not record.deleted and record.content.kind in {"note", "photo"}:
            originals.append({"scene_id": scene.id, "record": record.model_dump(mode="json")})
        frame["journey"] = extract_journey(
            frames[-1] if frames else None, frame, route.evidence if route else None, route_revision
        )
        previous = frames[-1] if frames else None
        comparable = (
            previous is not None
            and catalog is not None
            and frame["block"] is not None
            and frame["block"] == previous.get("block")
        )
        frame["relation_observations"] = (
            movement_observations(frame, previous, catalog) if comparable else None
        )
        plan = make_plan(frame, frames[-1] if frames else None, catalog, state)
        state = plan["state_after"]
        plans.append(plan)
        frames.append(frame)
    result = {
        "version": VERSION,
        "input_revision": base.input.source.revision(),
        "board_revision": base.board.plan_revision,
        "policy": policy.model_dump(mode="json"),
        "frames": frames,
        "plans": plans,
        "originals": originals,
        "road_snapshots": deepcopy(list(road_snapshots)),
        "scene_comparison_version": "scene-comparison-v1",
        "scene_backgrounds": scene_backgrounds,
    }
    return {"snapshot": deepcopy(result), "revision": digest(result)}
