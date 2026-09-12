"""Upload-only queries: lock the walk, load at most the requested chunk payload."""

from sqlalchemy import exists, select
from sqlalchemy.orm import raiseload, undefer

from daengs_backend.models import Walk, WalkPointChunk


def _walk(owner):
    return (
        select(Walk)
        .where(Walk.app_user_id == owner)
        .options(undefer(Walk.analysis_state), raiseload("*"))
        .execution_options(populate_existing=True)
        .with_for_update()
    )


async def by_client_session(db, owner, client_id):
    return await db.scalar(_walk(owner).where(Walk.client_session_id == client_id))


async def owned(db, owner, walk_id):
    return await db.scalar(_walk(owner).where(Walk.id == walk_id))


async def chunk(db, walk_id, seq_from):
    return await db.scalar(
        select(WalkPointChunk)
        .where(WalkPointChunk.walk_id == walk_id, WalkPointChunk.seq_from == seq_from)
        .execution_options(populate_existing=True)
    )


async def overlaps(db, walk_id, seq_from, seq_to):
    return await db.scalar(
        select(
            exists().where(
                WalkPointChunk.walk_id == walk_id,
                WalkPointChunk.seq_from != seq_from,
                WalkPointChunk.seq_from <= seq_to,
                WalkPointChunk.seq_to >= seq_from,
            )
        )
    )


def add_chunk(db, row):
    db.add(row)
