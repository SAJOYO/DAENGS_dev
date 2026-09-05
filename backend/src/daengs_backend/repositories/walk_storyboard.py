"""Queries run under the same Walk lock used by entry mutations and finalization."""

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from daengs_backend.models.walk import Walk, WalkAnalysis, WalkPet
from daengs_backend.models.walk_storyboard import WalkStoryboard


async def current(session, walk_id):
    return await session.get(WalkStoryboard, walk_id, populate_existing=True)


async def latest_analysis(session, walk_id):
    return await session.scalar(
        select(WalkAnalysis)
        .where(WalkAnalysis.walk_id == walk_id)
        .order_by(WalkAnalysis.derived_at.desc())
        .limit(1)
    )


async def reference_walks(session, walk):
    if len(walk.pet_ids) != 1:
        return []
    pet = walk.pet_ids[0]
    return list(
        await session.scalars(
            select(Walk)
            .where(
                Walk.app_user_id == walk.app_user_id,
                Walk.started_at < walk.started_at,
                Walk.analysis_state == "derived",
                Walk.pets.any(WalkPet.pet_id == pet),
                ~Walk.pets.any(WalkPet.pet_id != pet),
            )
            .options(selectinload(Walk.points), selectinload(Walk.pets))
            .order_by(Walk.started_at.desc(), Walk.id)
            .limit(3)
        )
    )
