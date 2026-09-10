"""Bounded public game reads. SQL ranks exact units before selecting a page or pet."""

from decimal import Decimal

from sqlalchemy import Numeric, cast, func, literal, select

from daengs_backend.models.activity import ActivityAccount
from daengs_backend.models.app_user import AppUser
from daengs_backend.models.pet import Pet
from daengs_backend.repositories.territory_owned import current_sites


async def active_member(db, member):
    return (
        await db.scalar(select(AppUser.id).where(AppUser.id == member, AppUser.status == "active"))
        is not None
    )


def pet_columns():
    return (
        Pet.id.label("pet_id"),
        Pet.name,
        Pet.breed,
        Pet.app_user_id,
        Pet.photo_storage_key.is_not(None).label("has_photo"),
        Pet.photo_updated_at,
    )


async def public_pet(db, pet_id, *, photo=False):
    columns = pet_columns()
    if photo:
        columns += (
            Pet.photo_storage_key,
            Pet.photo_content_type,
            Pet.photo_generation,
            Pet.photo_size_bytes,
        )
    # A registered pet becomes a public game profile only after game participation.
    participated = select(ActivityAccount.pet_id).where(ActivityAccount.pet_id == Pet.id).exists()
    return (await db.execute(select(*columns).where(Pet.id == pet_id, participated))).first()


def standings(season, rates):
    def number(key):
        # NUMERIC retains exact integer units even beyond bigint/JavaScript precision.
        return cast(ActivityAccount.score[key].astext, Numeric)

    multiplier = func.least(
        10_000 + func.greatest(0, number("scoring_count") - 1) * rates["extra_site_bps"],
        rates["maximum_bps"],
    )
    hourly = (number("current_count") - number("scoring_count")) * rates[
        "unverified_hourly"
    ] + number("scoring_count") * rates["scoring_hourly"]
    holding = (
        number("holding_units") + (season.confirmed_ms - number("last_ms")) * hourly * multiplier
    )
    total = number("bonus") * rates["point_denominator"] + holding
    return (
        select(
            *pet_columns(),
            total.label("total_units"),
            holding.label("holding_units"),
            number("base_bonus").label("base_points"),
            number("takeover_bonus").label("takeover_points"),
            # UUID orders tied rows deterministically, but does not break the shared rank.
            func.rank().over(order_by=total.desc()).label("rank"),
        )
        .join(ActivityAccount, ActivityAccount.pet_id == Pet.id)
        .where(ActivityAccount.season_id == season.id)
        .subquery()
    )


async def leaderboard(db, season, rates, after, limit):
    ranked = standings(season, rates)
    total = await db.scalar(
        select(func.count())
        .select_from(ActivityAccount)
        .where(ActivityAccount.season_id == season.id)
    )
    query = select(ranked)
    if after:
        units = literal(Decimal(after.units), type_=Numeric)
        query = query.where(
            (ranked.c.total_units < units)
            | ((ranked.c.total_units == units) & (ranked.c.pet_id > after.pet))
        )
    rows = await db.execute(
        query.order_by(ranked.c.total_units.desc(), ranked.c.pet_id).limit(limit + 1)
    )
    return total, rows.all()


async def standing(db, season, rates, pet_id):
    ranked = standings(season, rates)
    return (await db.execute(select(ranked).where(ranked.c.pet_id == pet_id))).first()


async def owned_counts(db, season_id, now, pets):
    if not pets:
        return {}
    current = current_sites(season_id, now).subquery()
    rows = await db.execute(
        select(
            current.c.pet_id,
            func.count().label("owned"),
            func.count().filter(current.c.certification == "VERIFIED").label("verified"),
        )
        .where(current.c.pet_id.in_(pets))
        .group_by(current.c.pet_id)
    )
    return {row.pet_id: (row.owned, row.verified) for row in rows}
