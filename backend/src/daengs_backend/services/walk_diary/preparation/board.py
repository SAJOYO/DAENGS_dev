"""Opt-in saved-input preparation, reusing the existing owner check and analysis replay.

No API exposure, writes, provider request, generation reservation or new orchestrator.
The caller owns the DB transaction just as with prepare_saved_diary.
"""

import uuid
from dataclasses import dataclass, replace

from daengs_backend.orchestration.contracts import PrincipalContext
from daengs_backend.services.walk_diary.guard import require_owner
from daengs_backend.services.walk_diary.preparation.input import InputAssembly, read_input
from daengs_backend.services.walk_diary.preparation.route_policy import configured_route_patterns
from daengs_walk.diary_board import (
    BaseBoard,
    BaseBoardPolicy,
    PreparedBaseBoard,
    VerifiedBoardRoute,
)
from daengs_walk.diary_board_assembly import assemble_base_board
from daengs_walk.diary_board_selection import prepare_base_board
from daengs_walk.diary_scene_backgrounds import SceneBackgroundSnapshot
from daengs_walk.diary_slots import BoardSlotSnapshot, SlotPolicy, prepare_board_slots


@dataclass(frozen=True)
class PreparedSavedBaseBoard:
    input: InputAssembly
    plan: PreparedBaseBoard
    board: BaseBoard
    slots: BoardSlotSnapshot
    scene_backgrounds: SceneBackgroundSnapshot | None = None
    cached_jobs: tuple[dict, ...] = ()


def with_scene_backgrounds(prepared, snapshot):
    observation = prepared.input.observation_source
    route = (
        VerifiedBoardRoute(observation.route, observation.evidence)
        if observation is not None and observation.evidence is not None
        else None
    )
    snapshot = snapshot.validate_board(prepared.board)
    slots = prepare_board_slots(
        prepared.input.source,
        prepared.board,
        prepared.slots.policy,
        route=route,
        scene_backgrounds=snapshot,
    )
    return replace(prepared, slots=slots, scene_backgrounds=snapshot)


def assemble_saved_base_board(
    assembled: InputAssembly, policy: BaseBoardPolicy, *, slot_policy: SlotPolicy | None = None
):
    observation = assembled.observation_source
    route = (
        VerifiedBoardRoute(observation.route, observation.evidence)
        if observation is not None and observation.evidence is not None
        else None
    )
    plan = prepare_base_board(assembled.source, policy, route=route)
    board = assemble_base_board(assembled.source, plan, route=route)
    chosen_policy = (
        slot_policy if slot_policy is not None else configured_route_patterns(SlotPolicy())
    )
    slots = prepare_board_slots(assembled.source, board, chosen_policy, route=route)
    return PreparedSavedBaseBoard(assembled, plan, board, slots)


async def prepare_saved_base_board(
    session,
    principal: PrincipalContext,
    walk_id,
    policy: BaseBoardPolicy,
    *,
    slot_policy: SlotPolicy | None = None,
):
    if principal.kind != "APP_USER":
        raise PermissionError("diary requires its walk owner")
    assembled = await read_input(session, uuid.UUID(principal.subject), walk_id)
    require_owner(principal, assembled.source)
    return assemble_saved_base_board(assembled, policy, slot_policy=slot_policy)
