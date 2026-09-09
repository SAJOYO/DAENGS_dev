"""Queries only. Call mutations under the existing owned-walk row lock."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB

from daengs_backend.models.walk import WalkPointChunk
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.models.walk_entry_v2 import WalkEntryMutation, WalkEntryPin


async def pin(session, walk_id, entry_id):
    return await session.get(WalkEntryPin, (walk_id, entry_id))


async def pins(session, walk_ids):
    return list(
        await session.scalars(select(WalkEntryPin).where(WalkEntryPin.walk_id.in_(walk_ids)))
    )


async def receipt(session, walk_id, entry_id, mutation_id):
    return await session.get(WalkEntryMutation, (walk_id, entry_id, mutation_id))


async def raw_chunks(session, walk_id):
    return list(
        await session.scalars(select(WalkPointChunk).where(WalkPointChunk.walk_id == walk_id))
    )


async def contains_v2(session, walk_ids, *, entry_id=None):
    query = select(WalkEntryPin.entry_id).where(WalkEntryPin.walk_id.in_(walk_ids))
    if entry_id is None:
        query = query.join(
            WalkEntry,
            (WalkEntry.walk_id == WalkEntryPin.walk_id) & (WalkEntry.id == WalkEntryPin.entry_id),
        ).where(
            WalkEntry.payload.is_not(None),
            WalkEntry.payload != JSONB.NULL,
        )
    else:
        query = query.where(WalkEntryPin.entry_id == entry_id)
    return await session.scalar(query.limit(1)) is not None
