"""Explicit historical slot writing and completion. Default card writing lives in runtime."""

from daengs_backend.services.walk_diary.legacy.slots import (
    assemble_slot_writing,
    generate_slot_prose,
    write_slot_stamps,
)
from daengs_walk.diary.board.output import publish_board


async def write_legacy_slot_board(source, base, generate=generate_slot_prose):
    """Use the historical slot payload explicitly; injecting a model keeps that contract."""
    if source.revision() != base.board.input_revision:
        raise ValueError("board writer requires its prepared source")
    return await write_slot_stamps(base.board, base.slots, generate)


def complete_slot_board(prepared, output):
    base = prepared.board
    if prepared.input.source.revision() != base.board.input_revision:
        raise ValueError("board completion requires its prepared source")
    board = assemble_slot_writing(base.board, base.slots, output)
    return publish_board(board, base.plan)
