"""Prepare an opt-in relation strategy using real board sources, without persistence."""

from copy import deepcopy
from dataclasses import replace

from daengs_backend.services.walk_diary.writing.context import get_action_context, get_space_context
from daengs_walk.diary.board.models import VerifiedBoardRoute
from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.relational.planning import VERSION, make_plan
from daengs_walk.diary.route.movement import prepare_movement
from daengs_walk.diary.slots.service import prepare_board_slots


def prepare_relational_diary(base, *, scene_ids=None, road_snapshots=()):
    """Keep full eligible facts; selected scenes define the comparison intervals."""
    observation = base.input.observation_source
    route = (
        VerifiedBoardRoute(observation.route, observation.evidence)
        if observation is not None and observation.evidence is not None
        else None
    )
    eligible = {}
    slots = prepare_board_slots(
        base.input.source,
        base.board,
        base.slots.policy,
        route=route,
        scene_backgrounds=base.scene_backgrounds,
        eligible_frames=eligible,
    )
    prepared = replace(base, slots=slots)
    policy = slots.policy
    catalog = (
        prepare_movement(base.input.source, route, policy.movement, policy.route_patterns)
        if policy.movement is not None
        else None
    )
    selected = set(scene_ids) if scene_ids is not None else {s.id for s in base.board.scenes}
    if not selected <= {s.id for s in base.board.scenes}:
        raise ValueError("unknown relation scene")
    frames, plans, originals = [], [], []
    state = None
    for scene in sorted(base.board.scenes, key=lambda s: (s.anchor.event_at, s.id)):
        if scene.id not in selected:
            continue
        action = get_action_context(prepared, scene.id)
        # Narration is shared, but action-only motion aliases must not collide with space.
        action_data = action.llm_input if action else None
        if action_data and "recorded_action" not in action_data:
            action_data = {
                "recorded_action": {
                    "id": "a1",
                    "actor": action_data.get("actor"),
                    "action": action_data["action"],
                }
            }
        space = get_space_context(
            prepared, scene.id, eligible_materials=eligible[scene.id]
        ).llm_input
        motion = [e for e in eligible[scene.id] if e.role == "scene_movement"]
        blocks = {c["block"] for e in motion for c in e.facts["claims"]}
        frame = {
            "scene_id": scene.id,
            "anchor": scene.anchor.model_dump(mode="json"),
            "at_s": (scene.anchor.event_at - base.input.source.started_at).total_seconds(),
            "block": next(iter(blocks)) if len(blocks) == 1 else None,
            "space": space,
            "action": action_data,
            "eligible_evidence": [e.model_dump(mode="json") for e in eligible[scene.id]],
        }
        for snapshot in road_snapshots:
            if snapshot.get("point") != frame["anchor"].get("point"):
                continue
            body = snapshot.get("response", {})
            rows = body.get("result", [])
            if snapshot.get("addr_type") != 10 or body.get("errCd") != 0 or len(rows) != 1:
                continue
            name = rows[0].get("road_nm")
            if isinstance(name, str) and name.strip() and name.lower() != "null":
                frame["road_reference"] = {
                    "id": "road1",
                    "road_nm": name,
                    "scope": "좌표에 대응한 주소의 도로명. 실제 걸은 도로·진입은 미확인",
                }
        record = getattr(scene.core, "record", None)
        if record is not None and not record.deleted and record.content.kind in {"note", "photo"}:
            originals.append({"scene_id": scene.id, "record": record.model_dump(mode="json")})
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
    }
    return {"snapshot": deepcopy(result), "revision": digest(result)}
