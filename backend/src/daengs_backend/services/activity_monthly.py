"""Opt-in monthly succession, atomic with final scores and neutralization. Caller owns commit."""

from daengs_backend.models.activity import ActivityMonthlySeason, ActivitySeason
from daengs_backend.repositories import activity as repo
from daengs_backend.services.activity_core.first_season_rewards import REWARD_VERSION
from daengs_backend.services.activity_core.game_policy import require
from daengs_backend.services.activity_core.monthly_calendar import month


async def rollover(db, at_ms):
    from daengs_backend.services import activity_game

    season = await repo.active_season(db)
    # No season means no automatic first activation. Manual seasons never opt in implicitly.
    if season is None or await db.get(ActivityMonthlySeason, season.id) is None:
        return
    while at_ms >= season.ends_ms:
        require(season.rules.get("version") == REWARD_VERSION, "monthly_policy_mismatch")
        require(month(season.starts_ms)[2] == season.ends_ms, "monthly_boundary_mismatch")
        await activity_game._close_if_due(db, season.ends_ms)
        await db.flush()  # Release the one-active-season constraint before creating its successor.
        successor_id, starts_ms, ends_ms = month(season.ends_ms)
        require(await db.get(ActivitySeason, successor_id) is None, "monthly_season_conflict")
        successor = ActivitySeason(
            id=successor_id,
            starts_ms=starts_ms,
            ends_ms=ends_ms,
            coverage_start_ms=starts_ms,
            confirmed_ms=starts_ms,
            revision=1,
            status="ACTIVE",
            rules=dict(season.rules),
        )
        db.add(successor)
        await db.flush()
        db.add(ActivityMonthlySeason(season_id=successor_id, previous_season_id=season.id))
        await db.flush()
        season = successor
