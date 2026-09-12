"""Queries only. All backup mutations serialize on the existing walk row lock."""

from sqlalchemy import select, text
from sqlalchemy.orm import undefer

from daengs_backend.models import Walk, WalkPointChunk
from daengs_backend.models.walk_motion import WalkMotionBackup, WalkMotionChunk


async def available(session):
    return bool(
        await session.scalar(
            text(
                "SELECT to_regclass('walk_motion_backups') IS NOT NULL "
                "AND to_regclass('walk_motion_chunks') IS NOT NULL"
            )
        )
    )


async def owned_locked(session, owner, walk_id):
    return await session.scalar(
        select(Walk)
        .where(Walk.id == walk_id, Walk.app_user_id == owner)
        .options(undefer(Walk.analysis_state))
        .with_for_update()
    )


async def backup(session, walk_id):
    return await session.get(WalkMotionBackup, walk_id)


async def chunks(session, walk_id):
    return list(
        await session.scalars(
            select(WalkMotionChunk)
            .where(WalkMotionChunk.walk_id == walk_id)
            .order_by(WalkMotionChunk.chunk_index)
        )
    )


async def chunk_indices(session, walk_id):
    return list(
        await session.scalars(
            select(WalkMotionChunk.chunk_index)
            .where(WalkMotionChunk.walk_id == walk_id)
            .order_by(WalkMotionChunk.chunk_index)
        )
    )


async def chunk(session, walk_id, index):
    return await session.get(WalkMotionChunk, (walk_id, index))


async def raw_chunks(session, walk_id, start=None, end=None):
    statement = select(WalkPointChunk).where(WalkPointChunk.walk_id == walk_id)
    if start is not None:
        statement = statement.where(WalkPointChunk.seq_to >= start, WalkPointChunk.seq_from < end)
    return list(await session.scalars(statement.order_by(WalkPointChunk.seq_from)))
