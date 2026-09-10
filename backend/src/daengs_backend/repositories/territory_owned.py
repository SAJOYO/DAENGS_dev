"""Read current ownership through the active season's open holding periods."""

from sqlalchemy import func, select

from daengs_backend.models.activity import ActivityHoldingPeriod
from daengs_backend.models.pet import Pet
from daengs_backend.models.territory_claim import (
    TerritoryClaim,
    TerritoryClaimSession,
    TerritoryClaimSite,
    TerritoryOccupancy,
)


async def page(db, owner, season_id, now, pet_id, after, limit):
    # Open periods identify the season; the current occupancy is the source of truth
    # for possession. Historical attempts and aggregate score counts are not a list.
    current = (
        select(
            TerritoryClaimSite.site_id,
            TerritoryClaimSite.version,
            Pet.id.label("pet_id"),
            Pet.name.label("pet_name"),
            Pet.breed.label("pet_breed"),
            TerritoryOccupancy.certification,
            TerritoryOccupancy.occupied_at,
            TerritoryOccupancy.expires_at,
        )
        .select_from(ActivityHoldingPeriod)
        .join(TerritoryOccupancy, TerritoryOccupancy.site_id == ActivityHoldingPeriod.site_id)
        .join(TerritoryClaim, TerritoryClaim.id == TerritoryOccupancy.claim_id)
        .join(TerritoryClaimSession, TerritoryClaimSession.id == TerritoryClaim.session_id)
        .join(Pet, Pet.id == TerritoryClaim.pet_id)
        .join(TerritoryClaimSite, TerritoryClaimSite.site_id == TerritoryOccupancy.site_id)
        .where(
            ActivityHoldingPeriod.season_id == season_id,
            ActivityHoldingPeriod.ended_ms.is_(None),
            ActivityHoldingPeriod.claim_id == TerritoryOccupancy.claim_id,
            ActivityHoldingPeriod.pet_id == Pet.id,
            TerritoryClaimSession.app_user_id == owner,
            Pet.app_user_id == owner,
            # Legacy pre-lease seasons can have null deadlines.
            (TerritoryOccupancy.expires_at.is_(None)) | (TerritoryOccupancy.expires_at > now),
        )
    )
    if pet_id is not None:
        current = current.where(Pet.id == pet_id)
    total = await db.scalar(select(func.count()).select_from(current.subquery()))
    if after is not None:
        current = current.where(TerritoryClaimSite.site_id > after)
    rows = (await db.execute(current.order_by(TerritoryClaimSite.site_id).limit(limit + 1))).all()
    return total, rows
