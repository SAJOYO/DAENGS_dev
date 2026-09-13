"""Write from fixed part stamps; publish through the existing board projection."""

from daengs_backend.services.walk_diary_card_writing import (
    CardWritingResult,
    complete_cards,
    write_cards,
)
from daengs_backend.services.walk_diary_slot_writing import (
    assemble_slot_writing,
    write_slot_stamps,
)
from daengs_walk.diary_board_output import publish_board


async def write_board(source, base, generate=None):
    if source.revision() != base.board.input_revision:
        raise ValueError("board writer requires its prepared source")
    if generate is not None:
        # Historical slot/preview tests can still exercise the previous contract explicitly.
        return await write_slot_stamps(base.board, base.slots, generate)
    from daengs_backend.services.walk_diary_space_collection import configured_collection

    return await write_cards(source, base, collector=configured_collection)


def complete_slot_board(prepared, output):
    if isinstance(output, CardWritingResult):
        return complete_cards(prepared, output)
    base = prepared.board
    if prepared.input.source.revision() != base.board.input_revision:
        raise ValueError("board completion requires its prepared source")
    board = assemble_slot_writing(base.board, base.slots, output)
    return publish_board(board, base.plan)
