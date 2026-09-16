"""Exclusion-first, per-scene part slots. No provider calls, prose or persistent cache."""

from daengs_walk.diary.board.models import (
    BaseBoard,
    VerifiedBoardRoute,
)
from daengs_walk.diary.contracts.input import DiaryInput
from daengs_walk.diary.contracts.slots import BoardSlotSnapshot, SlotPolicy
from daengs_walk.diary.slots.admission import admit, eligible_candidates


def _prepare_board_evidence(
    source: DiaryInput,
    board: BaseBoard,
    policy: SlotPolicy,
    *,
    route: VerifiedBoardRoute | None = None,
    scene_backgrounds=None,
    frozen_motion: BoardSlotSnapshot | None = None,
    eligible_frames: dict | None = None,
    eligibility_only: bool = False,
) -> BoardSlotSnapshot | dict:
    """Apply part rules to already-selected scenes without selecting a second board."""
    from daengs_walk.diary.slots.sources import candidates_for_scene, verified_motion

    if (
        board.client_session_id != source.client_session_id
        or board.input_revision != source.revision()
    ):
        raise ValueError("part slots require the selected board's source snapshot")
    extra = (
        scene_backgrounds.validate_board(board).backgrounds if scene_backgrounds is not None else ()
    )
    if {b.id for b in extra} & {b.id for b in source.backgrounds}:
        raise ValueError("collected and stored background IDs overlap")
    motion, blocks = verified_motion(source, route)
    movement = None
    if frozen_motion is not None and (
        frozen_motion.input_revision != source.revision()
        or frozen_motion.plan_revision != board.plan_revision
        or frozen_motion.policy != policy
    ):
        raise ValueError("frozen movement belongs to another preparation")
    if policy.movement is not None and frozen_motion is None:
        from daengs_walk.diary.route.movement import prepare_movement

        movement = prepare_movement(source, route, policy.movement, policy.route_patterns)
    patterns = None
    if policy.route_patterns is not None and policy.movement is None:
        from daengs_walk.diary.slots.route import prepare_route_patterns

        patterns = prepare_route_patterns(source, route, policy.route_patterns)
    stamps = []
    eligible_result = {}
    for scene in board.scenes:
        candidates, decisions = candidates_for_scene(
            source,
            scene,
            policy,
            motion if policy.movement is None else (),
            blocks,
            extra_backgrounds=extra,
        )
        if policy.movement is not None:
            if frozen_motion is not None:
                prior = next(s for s in frozen_motion.stamps if s.scene_id == scene.id)
                candidates.extend(e for e in prior.evidence if e.part == "motion")
                decisions.extend(
                    d for d in prior.decisions if d.part == "motion" and d.admission != "kept"
                )
            else:
                from daengs_walk.diary.slots.movement import movement_candidates

                candidates.extend(
                    movement_candidates(scene, board.scenes, movement, policy, decisions)
                )
        elif policy.route_patterns is not None:
            from daengs_walk.diary.slots.route import pattern_candidates

            candidates.extend(pattern_candidates(scene, patterns, policy, decisions))
        if eligibility_only:
            eligible_result[scene.id] = tuple(eligible_candidates(candidates, decisions, policy))
            continue
        frame = [] if eligible_frames is not None else None
        stamps.append(admit(scene.id, candidates, decisions, policy, eligible_out=frame))
        if eligible_frames is not None:
            eligible_frames[scene.id] = tuple(frame)
    if eligibility_only:
        return eligible_result
    return BoardSlotSnapshot(
        client_session_id=board.client_session_id,
        input_revision=board.input_revision,
        plan_revision=board.plan_revision,
        policy=policy,
        stamps=tuple(stamps),
    )


def prepare_board_slots(
    source,
    board,
    policy,
    *,
    route=None,
    scene_backgrounds=None,
    frozen_motion=None,
    eligible_frames=None,
):
    """Historical capacity selection over shared source eligibility."""
    return _prepare_board_evidence(
        source,
        board,
        policy,
        route=route,
        scene_backgrounds=scene_backgrounds,
        frozen_motion=frozen_motion,
        eligible_frames=eligible_frames,
    )


def prepare_eligible_scene_facts(source, board, policy, *, route=None, scene_backgrounds=None):
    """Complete valid evidence per selected scene; never run writing capacity selection."""
    return _prepare_board_evidence(
        source,
        board,
        policy,
        route=route,
        scene_backgrounds=scene_backgrounds,
        eligibility_only=True,
    )
