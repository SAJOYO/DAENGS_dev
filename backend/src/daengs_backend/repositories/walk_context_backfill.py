"""Bounded maintenance reads and queue writes; caller owns Walk locks and commit."""

import uuid

from sqlalchemy import and_, select

from daengs_backend.models.walk import Walk
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.models.walk_entry_context import WalkEntryContextEnvelope as Envelope
from daengs_backend.models.walk_entry_context import WalkEntryContextJob as Job
from daengs_backend.models.walk_entry_v2 import WalkEntryPin as Pin
from daengs_backend.models.walk_storyboard import WalkStoryboard


async def walk_ids(session, *, since, until, after, limit):
    query = select(Walk.id).where(
        Walk.started_at >= since,
        Walk.started_at < until,
        select(WalkEntry.id).where(WalkEntry.walk_id == Walk.id).exists(),
    )
    if after:
        query = query.where(Walk.id > after)
    return list(await session.scalars(query.order_by(Walk.id).limit(limit + 1)))


async def lock_walk(session, walk_id):
    return await session.scalar(select(Walk.id).where(Walk.id == walk_id).with_for_update())


async def has_board(session, walk_id):
    return await session.scalar(
        select(WalkStoryboard.walk_id).where(WalkStoryboard.walk_id == walk_id)
    )


async def records(session, walk_id):
    return list(
        await session.execute(
            select(WalkEntry, Pin)
            .outerjoin(Pin, and_(Pin.walk_id == WalkEntry.walk_id, Pin.entry_id == WalkEntry.id))
            .where(WalkEntry.walk_id == walk_id)
            .order_by(WalkEntry.id)
            .limit(601)
        )
    )


async def jobs(session, walk_id):
    return list(
        await session.execute(
            select(Job, Envelope)
            .outerjoin(
                Envelope,
                and_(
                    Envelope.job_id == Job.id,
                    Envelope.collection_round == Job.collection_round,
                    Envelope.attempt == Job.attempts,
                ),
            )
            .join(WalkEntry, and_(WalkEntry.walk_id == Job.walk_id, WalkEntry.id == Job.entry_id))
            .where(
                Job.walk_id == walk_id,
                Job.revision == WalkEntry.revision,
                Job.tag.in_(("space.address", "space.commerce", "space.park", "space.river")),
            )
        )
    )


async def schedule(session, slot, now, backfill_policy):
    # Terminal rows only; a claim never touches them. Walk lock serializes writers/backfills.
    if slot["job_id"] is None:
        row = Job(
            id=uuid.uuid4(),
            walk_id=uuid.UUID(slot["walk_id"]),
            entry_id=uuid.UUID(slot["entry_id"]),
            revision=slot["revision"],
            policy_version=slot["policy"],
            tag=slot["tag"],
            collection_round=0,
        )
        session.add(row)
    else:
        row = await session.get(Job, uuid.UUID(slot["job_id"]))
        row.collection_round += 1
    row.state, row.attempts, row.available_at = "pending", 0, now
    row.lease_token, row.lease_until = None, None
    row.backfill_policy = backfill_policy
