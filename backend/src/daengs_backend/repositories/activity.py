"""Queries/locks only. Services own policy decisions, mutations and commit."""

from datetime import UTC, datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload, undefer

from daengs_backend.models.activity import (
    ActivityAccount,
    ActivityHoldingPeriod,
    ActivitySeason,
    ActivitySessionLink,
    ActivityWalkHead,
)
from daengs_backend.models.territory_claim import (
    TerritoryClaimSession,
    TerritoryClaimSite,
    TerritoryOccupancy,
)
from daengs_backend.models.walk import Walk, WalkAnalysis


async def remove_owner(db, owner):
    await db.execute(delete(ActivitySessionLink).where(ActivitySessionLink.app_user_id == owner))
    await db.execute(
        delete(TerritoryClaimSession).where(TerritoryClaimSession.app_user_id == owner)
    )


async def latest_season(db):
    return await db.scalar(select(ActivitySeason).order_by(ActivitySeason.ends_ms.desc()).limit(1))


async def occupancies(db):
    return list(await db.scalars(select(TerritoryOccupancy)))


async def reset_occupancies(db):
    await db.execute(
        update(TerritoryClaimSite)
        .where(TerritoryClaimSite.site_id.in_(select(TerritoryOccupancy.site_id)))
        .values(version=TerritoryClaimSite.version + 1)
    )
    await db.execute(delete(TerritoryOccupancy))


async def pending_accounts(db, limit):
    return list(
        await db.scalars(
            select(ActivityAccount)
            .where(ActivityAccount.processed_revision < ActivityAccount.revision)
            .order_by(
                ActivityAccount.processed_revision,
                ActivityAccount.season_id,
                ActivityAccount.pet_id,
            )
            .limit(limit)
        )
    )


async def walks_in_window(db, owner, from_ms, to_ms):
    return list(
        await db.scalars(
            select(Walk)
            .where(
                Walk.app_user_id == owner,
                Walk.ended_at >= datetime.fromtimestamp(from_ms / 1000, UTC),
                Walk.ended_at < datetime.fromtimestamp(to_ms / 1000, UTC),
            )
            .options(selectinload(Walk.pets))
        )
    )


async def requeue(db):
    await db.execute(update(ActivityWalkHead).values(processed_revision=0))
    await db.execute(update(ActivityAccount).values(processed_revision=0))


async def barrier(db):
    await db.execute(text("SELECT pg_advisory_xact_lock(260,36)"))


async def link(db, owner, client, *, walk_id=None, game_session_id=None):
    values = {"app_user_id": owner, "client_session_id": client}
    updates = {}
    if walk_id is not None:
        updates["walk_id"] = walk_id
    if game_session_id is not None:
        updates["game_session_id"] = game_session_id
    await db.execute(
        insert(ActivitySessionLink)
        .values(**values, **updates)
        .on_conflict_do_update(
            index_elements=["app_user_id", "client_session_id"],
            set_=updates,
        )
    )


async def active_season(db):
    return await db.scalar(
        select(ActivitySeason).where(ActivitySeason.status == "ACTIVE").with_for_update()
    )


async def accounts(db, season_id):
    return list(
        await db.scalars(select(ActivityAccount).where(ActivityAccount.season_id == season_id))
    )


async def periods(db, season_id, pet_id=None):
    query = select(ActivityHoldingPeriod).where(ActivityHoldingPeriod.season_id == season_id)
    if pet_id is not None:
        query = query.where(ActivityHoldingPeriod.pet_id == pet_id)
    return list(
        await db.scalars(
            query.order_by(ActivityHoldingPeriod.start_order, ActivityHoldingPeriod.site_id)
        )
    )


async def open_period(db, season_id, site_id):
    return await db.scalar(
        select(ActivityHoldingPeriod).where(
            ActivityHoldingPeriod.season_id == season_id,
            ActivityHoldingPeriod.site_id == site_id,
            ActivityHoldingPeriod.ended_ms.is_(None),
        )
    )


async def walk_analysis(db, walk_id, analysis_id):
    walk = await db.scalar(
        select(Walk)
        .where(Walk.id == walk_id)
        .options(selectinload(Walk.pets), undefer(Walk.analysis_state))
    )
    analysis = await db.scalar(
        select(WalkAnalysis)
        .where(WalkAnalysis.id == analysis_id, WalkAnalysis.walk_id == walk_id)
        .options(selectinload(WalkAnalysis.capsule))
    )
    return walk, analysis


async def pending_walks(db, limit):
    return list(
        await db.scalars(
            select(ActivityWalkHead)
            .where(
                ActivityWalkHead.processed_revision < ActivityWalkHead.revision,
            )
            .order_by(ActivityWalkHead.walk_id)
            .limit(limit)
        )
    )
