"""Wire default card providers to the existing diary orchestrator. No legacy writer selection."""

from daengs_backend.services.walk_diary.writing.provider import generate_card_prose

__all__ = ["write_board", "write_cards"]


async def write_board(source, base, *, generate=None, collector=None):
    """Always use card orchestration, including when providers are injected."""
    if source.revision() != base.board.input_revision:
        raise ValueError("board writer requires its prepared source")
    from daengs_backend.services.walk_diary.collection.service import configured_collection

    return await write_cards(
        source,
        base,
        generate=generate,
        collector=configured_collection if collector is None else collector,
    )


async def write_cards(source, base, *, generate=None, collector=None):
    """Existing diary API entry; orchestration owns planning and execution."""
    from daengs_backend.orchestration.runtime import build_diary_orchestrator

    return await build_diary_orchestrator(
        generate=generate or generate_card_prose, collector=collector
    ).run(source, base)
