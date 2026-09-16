"""Queries only. All backup mutations serialize on the existing walk row lock."""

from sqlalchemy import select, text

from daengs_backend.models.walk_precision import WalkPrecisionBackup, WalkPrecisionChunk


async def available(session):
    return bool(
        await session.scalar(
            text(
                "SELECT to_regclass('walk_precision_backups') IS NOT NULL "
                "AND to_regclass('walk_precision_chunks') IS NOT NULL"
            )
        )
    )


async def backup(session, walk_id):
    return await session.get(WalkPrecisionBackup, walk_id)


async def chunks(session, walk_id):
    return list(
        await session.scalars(
            select(WalkPrecisionChunk)
            .where(WalkPrecisionChunk.walk_id == walk_id)
            .order_by(WalkPrecisionChunk.chunk_index)
        )
    )


async def chunk_indices(session, walk_id):
    return list(
        await session.scalars(
            select(WalkPrecisionChunk.chunk_index)
            .where(WalkPrecisionChunk.walk_id == walk_id)
            .order_by(WalkPrecisionChunk.chunk_index)
        )
    )


async def chunk(session, walk_id, index):
    return await session.get(WalkPrecisionChunk, (walk_id, index))
