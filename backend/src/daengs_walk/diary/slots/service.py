"""Exclusion-first, per-scene part slots. No provider calls, prose or persistent cache."""

from daengs_walk.diary.board.models import (
    BaseBoard,
    VerifiedBoardRoute,
)
from daengs_walk.diary.contracts.input import DiaryInput
from daengs_walk.diary.contracts.slots import BoardSlotSnapshot, SlotPolicy
from daengs_walk.diary.slots.admission import admit


def prepare_board_slots(
    source: DiaryInput,
    board: BaseBoard,
    policy: SlotPolicy,
    *,
    route: VerifiedBoardRoute | None = None,
    scene_backgrounds=None,
) -> BoardSlotSnapshot:
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
    patterns = None
    if policy.route_patterns is not None:
        from daengs_walk.diary.slots.route import prepare_route_patterns

        patterns = prepare_route_patterns(source, route, policy.route_patterns)
    stamps = []
    for scene in board.scenes:
        candidates, decisions = candidates_for_scene(
            source, scene, policy, motion, blocks, extra_backgrounds=extra
        )
        if policy.route_patterns is not None:
            from daengs_walk.diary.slots.route import pattern_candidates

            candidates.extend(pattern_candidates(scene, patterns, policy, decisions))
        stamps.append(admit(scene.id, candidates, decisions, policy))
    return BoardSlotSnapshot(
        client_session_id=board.client_session_id,
        input_revision=board.input_revision,
        plan_revision=board.plan_revision,
        policy=policy,
        stamps=tuple(stamps),
    )
