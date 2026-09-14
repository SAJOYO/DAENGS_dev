"""Preview envelope for an already assembled board and admitted materials."""

from typing import Literal

from pydantic import Field

from daengs_walk.diary.board.assembly import assemble_base_board
from daengs_walk.diary.board.models import BaseBoard, BaseBoardPolicy, BoardScene
from daengs_walk.diary.contracts.input import DiaryContract, Digest
from daengs_walk.diary.contracts.slots import PartStamp, SlotPolicy
from daengs_walk.diary.selection.board import prepare_base_board
from daengs_walk.diary.slots.service import prepare_board_slots


class SlotPreview(DiaryContract):
    format: Literal["walk-diary-slots-preview-v1"] = "walk-diary-slots-preview-v1"
    policy: SlotPolicy
    revision: Digest
    base_board: BaseBoard
    stamps: tuple[PartStamp, ...]
    scenes: tuple[BoardScene, ...]
    model_status: Literal["not_requested", "accepted", "unavailable"] = "not_requested"
    failure_code: Literal["provider_failed", "invalid_response", "budget_exceeded"] | None = None
    citations: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    writing_revision: Digest | None = None


def prepare_slot_preview(source, policy: SlotPolicy, board_policy: BaseBoardPolicy, *, route=None):
    plan = prepare_base_board(source, board_policy, route=route)
    board = assemble_base_board(source, plan, route=route)
    slots = prepare_board_slots(source, board, policy, route=route)
    return SlotPreview(
        policy=policy,
        revision=slots.revision(),
        base_board=board,
        stamps=slots.stamps,
        scenes=board.scenes,
    )
