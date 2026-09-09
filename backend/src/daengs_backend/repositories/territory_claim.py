"""Claim DAO. All writes are committed by the service; site rows serialize ownership."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from daengs_backend.models.pet import Pet
from daengs_backend.models.territory_claim import (
    TerritoryClaim,
    TerritoryClaimPhoto,
    TerritoryClaimSession,
    TerritoryClaimSite,
    TerritoryOccupancy,
)
from daengs_backend.repositories import pet as pet_repo


async def session_by_client(db, owner, client_id, *, lock=False):
    stmt = select(TerritoryClaimSession).where(
        TerritoryClaimSession.app_user_id == owner,
        TerritoryClaimSession.client_session_id == client_id,
    )
    if lock:
        stmt = stmt.with_for_update()
    return await db.scalar(stmt.execution_options(populate_existing=True))


async def insert_session(db, **values):
    await db.execute(
        insert(TerritoryClaimSession)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["app_user_id", "client_session_id"])
    )


async def eligible_pets(db, member, pet_ids):
    """점령에 데리고 나갈 수 있는 아이들 — **구성원(대표 ∪ 돌보미)** 기준입니다
    (docs/co-care.md §2).

    아빠가 걸어서 점령하려면 그 아이에 닿아야 합니다. **점령 결과의 소유는 안 바뀝니다** —
    `territory_claims.app_user_id` 가 그대로라 아빠가 먹은 땅은 아빠 것입니다 (결정 ①).

    배웅한 아이는 여기서 빠집니다. 기록은 남기되 새로 나가지는 않습니다.
    """
    return set(
        await db.scalars(
            select(Pet.id).where(
                Pet.id.in_(pet_ids),
                pet_repo._is_member(member),
                Pet.farewell_on.is_(None),
            )
        )
    )


async def lock_site(db, site_id):
    await db.execute(
        insert(TerritoryClaimSite).values(site_id=site_id, version=0).on_conflict_do_nothing()
    )
    return await db.scalar(
        select(TerritoryClaimSite)
        .where(TerritoryClaimSite.site_id == site_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def claim_at(db, session_id, site_id):
    return await db.scalar(
        select(TerritoryClaim)
        .where(TerritoryClaim.session_id == session_id, TerritoryClaim.site_id == site_id)
        .execution_options(populate_existing=True)
    )


async def owned_claim(db, owner, claim_id):
    return (
        await db.execute(
            select(TerritoryClaim, TerritoryClaimSession)
            .join(TerritoryClaimSession, TerritoryClaim.session_id == TerritoryClaimSession.id)
            .where(TerritoryClaim.id == claim_id, TerritoryClaimSession.app_user_id == owner)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()


async def claim_and_session(db, claim_id):
    return (
        await db.execute(
            select(TerritoryClaim, TerritoryClaimSession)
            .join(TerritoryClaimSession, TerritoryClaim.session_id == TerritoryClaimSession.id)
            .where(TerritoryClaim.id == claim_id)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()


async def occupancy(db, site_id):
    return await db.scalar(
        select(TerritoryOccupancy)
        .where(TerritoryOccupancy.site_id == site_id)
        .execution_options(populate_existing=True)
    )


async def photo_binding(db, photo_id):
    return await db.get(TerritoryClaimPhoto, photo_id)


async def read_sites(db, site_ids):
    # One statement: no mixed version/owner snapshot and no owner PII in the projection.
    return (
        await db.execute(
            select(
                TerritoryClaimSite.site_id,
                TerritoryClaimSite.version,
                TerritoryOccupancy.certification,
                TerritoryOccupancy.occupied_at,
                TerritoryOccupancy.certified_at,
                Pet.id.label("pet_id"),
                Pet.name.label("pet_name"),
                TerritoryClaimSession.app_user_id,
            )
            .outerjoin(TerritoryOccupancy, TerritoryOccupancy.site_id == TerritoryClaimSite.site_id)
            .outerjoin(TerritoryClaim, TerritoryClaim.id == TerritoryOccupancy.claim_id)
            .outerjoin(TerritoryClaimSession, TerritoryClaimSession.id == TerritoryClaim.session_id)
            .outerjoin(Pet, Pet.id == TerritoryClaim.pet_id)
            .where(TerritoryClaimSite.site_id.in_(site_ids))
        )
    ).all()
