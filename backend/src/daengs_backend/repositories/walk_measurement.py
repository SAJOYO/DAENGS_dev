"""Queries only; mutations share the parent walk's ownership/deletion lock."""

from sqlalchemy import select, text

from daengs_backend.models.walk_measurement import WalkMeasurement, WalkMeasurementChunk


async def available(session):
    return bool(
        await session.scalar(
            text(
                "SELECT to_regclass('walk_measurements') IS NOT NULL "
                "AND to_regclass('walk_measurement_chunks') IS NOT NULL"
            )
        )
    )


async def by_input(session, walk_id, key):
    return await session.scalar(
        select(WalkMeasurement).where(
            WalkMeasurement.walk_id == walk_id, WalkMeasurement.input_key == key
        )
    )


async def read(session, walk_id, measurement_id, index=None):
    if index is None:
        return await session.get(WalkMeasurement, (walk_id, measurement_id))
    return await session.get(WalkMeasurementChunk, (walk_id, measurement_id, index))
