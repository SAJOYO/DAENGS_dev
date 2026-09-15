"""Reuse saved scene/route selection, without executing the legacy slot admission pipeline."""

from daengs_backend.services.walk_diary.preparation.board import PreparedSavedBaseBoard
from daengs_backend.services.walk_diary.preparation.route_policy import configured_route_patterns
from daengs_walk.diary.board.assembly import assemble_base_board
from daengs_walk.diary.board.models import BaseBoardPolicy, VerifiedBoardRoute
from daengs_walk.diary.contracts.slots import BoardSlotSnapshot, SlotPolicy
from daengs_walk.diary.selection.board import prepare_base_board
from daengs_walk.diary.selection.stamps import StampPolicy


def assemble_relational_base(assembled, target):
    observation = assembled.observation_source
    route = (
        VerifiedBoardRoute(observation.route, observation.evidence)
        if (observation is not None and observation.evidence is not None)
        else None
    )
    policy = BaseBoardPolicy(intermediate=StampPolicy(target_scene_count=target))
    plan = prepare_base_board(assembled.source, policy, route=route)
    board = assemble_base_board(assembled.source, plan, route=route)
    # Only the shared eligibility/measurement policy is consumed by new preparation.
    slots = BoardSlotSnapshot(
        client_session_id=assembled.source.client_session_id,
        input_revision=assembled.source.revision(),
        plan_revision=plan.revision(),
        policy=configured_route_patterns(SlotPolicy()),
        stamps=(),
    )
    return PreparedSavedBaseBoard(assembled, plan, board, slots)
