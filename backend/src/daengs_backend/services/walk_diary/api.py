"""Public service entry points; lazy dispatch keeps read and write dependencies separate."""

__all__ = [
    "existing_format",
    "generate_diary",
    "generate_relational",
    "get_diary",
    "get_relational",
    "get_slot_writer",
    "guard_old_writer",
    "is_relational",
    "legacy_slot_writer",
    "preview_saved_slots",
]


def is_relational(row):
    from .lifecycle.relational import is_relational as check

    return check(row)


async def get_relational(session, owner, walk_id, target=None):
    from .lifecycle.relational import get_relational as read

    return await read(session, owner, walk_id, target)


async def generate_relational(session, owner, walk_id, request, *, writer=None, policy=None):
    from .lifecycle.relational import generate_relational as generate

    return await generate(session, owner, walk_id, request, writer=writer, policy=policy)


def legacy_slot_writer(writer=None):
    """Explicitly bind a historical slot writer; ordinary provider overrides stay cards."""
    from .writer_contract import LegacySlotWriter

    return LegacySlotWriter(writer)


async def existing_format(session, owner, walk_id, target):
    from .lifecycle.negotiation import existing_format as negotiate

    return await negotiate(session, owner, walk_id, target)


def guard_old_writer(row):
    from .lifecycle.negotiation import guard_old_writer as guard

    return guard(row)


async def get_diary(session, owner, walk_id, target, bundle_format="walk-diary-bundle-v1"):
    from .lifecycle.generation import get_diary as read

    return await read(session, owner, walk_id, target, bundle_format)


async def generate_diary(
    session,
    owner,
    walk_id,
    request,
    *,
    writer=None,
    legacy_collector=None,
    legacy_context_wait=False,
):
    from .lifecycle.generation import generate_diary as generate

    return await generate(
        session,
        owner,
        walk_id,
        request,
        writer=writer,
        legacy_collector=legacy_collector,
        legacy_context_wait=legacy_context_wait,
    )


def get_slot_writer():
    from .legacy.slots import write_slot_preview

    return write_slot_preview


async def preview_saved_slots(session, owner, walk_id, request, *, writer=None, collector=None):
    from .preview import preview_saved_slots as preview

    overrides = {}
    if writer is not None:
        overrides["writer"] = writer
    if collector is not None:
        overrides["collector"] = collector
    return await preview(session, owner, walk_id, request, **overrides)
