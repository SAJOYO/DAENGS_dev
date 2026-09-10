"""Queries only; serialize reads/writes with the existing owned-walk row lock."""

from daengs_backend.models.walk_photo import WalkPhotoManifest


async def current(session, walk_id):
    return await session.get(WalkPhotoManifest, walk_id, populate_existing=True)
