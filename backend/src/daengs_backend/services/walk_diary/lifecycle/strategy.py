"""Bind writing, accepted result and completion before executing an external writer."""

from collections.abc import Callable
from dataclasses import dataclass

from daengs_backend.services.walk_diary.writer_contract import LegacySlotWriter
from daengs_walk.diary.board.output import BOARD_FORMAT


@dataclass(frozen=True)
class GenerationStrategy:
    write: Callable
    result_type: type
    finish: Callable
    collects_backgrounds: bool = False

    def validate(self, output):
        if not isinstance(output, self.result_type):
            raise TypeError("writer result does not match the selected generation contract")


def _cards(prepared, output, revision, ticket):
    from daengs_backend.services.walk_diary.storage.board import store_board
    from daengs_backend.services.walk_diary.writing.assembly import complete_cards

    return store_board(prepared, complete_cards(prepared, output), revision, writing=output)


def _slots(prepared, output, revision, ticket):
    from daengs_backend.services.walk_diary.legacy.board_slots import complete_slot_board
    from daengs_backend.services.walk_diary.storage.board import store_board

    return store_board(prepared, complete_slot_board(prepared, output), revision, writing=output)


def _bundle(prepared, output, revision, ticket):
    from daengs_backend.services.walk_diary.storage.bundle import store_diary

    if (
        output.input_revision != ticket.input_revision
        or output.plan_revision != prepared.prepared.plan.revision()
    ):
        raise ValueError("writer returned another generation's bundle")
    return store_diary(prepared, output, revision)


def select_strategy(bundle_format, writer=None):
    legacy_slots = isinstance(writer, LegacySlotWriter)
    override = writer.writer if legacy_slots else writer
    if bundle_format == BOARD_FORMAT:
        if legacy_slots:
            from daengs_backend.services.walk_diary.legacy.board_slots import (
                write_legacy_slot_board,
            )
            from daengs_backend.services.walk_diary.legacy.slots import SlotWritingResult

            return GenerationStrategy(
                override or write_legacy_slot_board, SlotWritingResult, _slots
            )
        from daengs_backend.services.walk_diary.contracts import CardWritingResult
        from daengs_backend.services.walk_diary.runtime import write_board

        return GenerationStrategy(override or write_board, CardWritingResult, _cards, True)
    if bundle_format == "walk-diary-bundle-v1":
        from daengs_backend.services.walk_diary.legacy.bundle import write_diary
        from daengs_walk.diary.contracts.output import DiaryBundle

        return GenerationStrategy(override or write_diary, DiaryBundle, _bundle)
    raise ValueError("unsupported diary generation format")
