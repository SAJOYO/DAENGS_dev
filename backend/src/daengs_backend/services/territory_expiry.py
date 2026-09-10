"""First-season leases. Caller holds the activity barrier and owns the transaction."""

from dataclasses import asdict, replace
from datetime import UTC, datetime

from daengs_backend.models.territory_claim import TerritoryClaimSite
from daengs_backend.repositories import activity as repo
from daengs_backend.services.activity_core import first_season_policy as first
from daengs_backend.services.activity_core.first_season_rewards import REWARD_VERSION
from daengs_backend.services.activity_core.game_policy import require

OWNERSHIP_MS = 72 * 60 * 60 * 1000


def enabled(season):
    return season is not None and season.rules.get("version") == REWARD_VERSION


def deadline(at_ms, season):
    require(season.starts_ms <= at_ms < season.ends_ms, "season_ended")
    return datetime.fromtimestamp(min(at_ms + OWNERSHIP_MS, season.ends_ms) / 1000, UTC)


async def expire_due(db, season, at_ms):
    """Split aggregate holding balances at EVERY expired site's actual end, even after downtime."""
    if not enabled(season):
        return
    due = await repo.expiring_occupancies(db, min(at_ms, season.ends_ms))
    if not due:
        return
    accounts = {row.pet_id: row for row in await repo.accounts(db, season.id)}
    rules = first.Rules(**season.rules)
    for occupied, period in due:
        # The query joins the currently active season's open holding period.
        require(period.season_id == season.id, "holding_season_mismatch")
        end = int(occupied.expires_at.timestamp() * 1000)
        require(end >= season.confirmed_ms, "expiry_before_confirmed_cut")
        account = accounts.get(period.pet_id)
        require(account is not None, "holding_source_missing")
        score = first.settle(first.Score(**account.score), end, rules)
        account.score = asdict(
            replace(
                score,
                current_count=score.current_count - 1,
                scoring_count=score.scoring_count - int(occupied.certification == "VERIFIED"),
            )
        )
        season.revision += 1
        season.confirmed_ms = end
        account.revision = season.revision
        period.ended_ms, period.end_order = end, season.revision
        site = await db.get(TerritoryClaimSite, occupied.site_id, with_for_update=True)
        site.version += 1
        await db.delete(occupied)
    await db.flush()
