"""Explicit card and historical slot entries; share the existing board projection."""

from daengs_backend.services.walk_diary_card_assembly import complete_cards
from daengs_backend.services.walk_diary_card_contracts import CardWritingResult
from daengs_backend.services.walk_diary_card_writing import write_cards
from daengs_backend.services.walk_diary_slot_writing import (
    assemble_slot_writing,
    generate_slot_prose,
    write_slot_stamps,
)
from daengs_walk.diary_board_output import publish_board


async def write_board(source, base, *, generate=None, collector=None):
    """Always use card orchestration, including when providers are injected."""
    if source.revision() != base.board.input_revision:
        raise ValueError("board writer requires its prepared source")
    from daengs_backend.services.walk_diary_space_collection import configured_collection

    return await write_cards(
        source,
        base,
        generate=generate,
        collector=configured_collection if collector is None else collector,
    )


async def write_legacy_slot_board(source, base, generate=generate_slot_prose):
    """Use the historical slot payload explicitly; injecting a model keeps that contract."""
    if source.revision() != base.board.input_revision:
        raise ValueError("board writer requires its prepared source")
    return await write_slot_stamps(base.board, base.slots, generate)


def complete_slot_board(prepared, output):
    if isinstance(output, CardWritingResult):
        return complete_cards(prepared, output)
    base = prepared.board
    if prepared.input.source.revision() != base.board.input_revision:
        raise ValueError("board completion requires its prepared source")
    board = assemble_slot_writing(base.board, base.slots, output)
    return publish_board(board, base.plan)
