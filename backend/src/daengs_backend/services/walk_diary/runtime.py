"""Explicit service entries for relational receipts and historical card contracts."""

__all__ = ["write_board", "write_cards", "write_relational_board"]


async def generate_card_prose(stage, payload, schema):
    """Load the historical provider only when the historical service is called."""
    from daengs_backend.services.walk_diary.writing.provider import generate_card_prose as generate

    return await generate(stage, payload, schema)


async def write_relational_board(
    source,
    base,
    *,
    scene_ids=None,
    prepare=None,
    send=None,
    execution_policy=None,
):
    """Service entry returning the new receipt; old API/DB activation is a separate step."""
    from daengs_backend.orchestration.runtime import build_relational_diary_orchestrator

    return await build_relational_diary_orchestrator(
        prepare=prepare,
        send=send,
        execution_policy=execution_policy,
    ).run(source, base, scene_ids=scene_ids)


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
