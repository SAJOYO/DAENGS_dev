"""Read a selected site's owner and score from one read-only database snapshot."""

from daengs_backend.models.activity import ActivityAccount
from daengs_backend.repositories import activity as activity_repo
from daengs_backend.repositories import territory_claim as territory_repo
from daengs_backend.services import activity, activity_game
from daengs_backend.services.activity_core.game_policy import POINT_DENOMINATOR


def display_points(score):
    """Same one-decimal truncation as APP HomeGameCard, without float/Decimal rounding."""
    tenths = (score["bonus"] * POINT_DENOMINATOR + score["holding_units"]) * 10
    whole, fraction = divmod(tenths // POINT_DENOMINATOR, 10)
    return f"{whole}.{fraction}" if fraction else str(whole)


async def summary(db, viewer_id, site_id):
    # This new endpoint requires the existing activity schema/flag. Disabled is not zero.
    activity.enabled()
    rows = await territory_repo.read_sites(db, [site_id])
    row = rows[0] if rows else None
    season = await activity_repo.read_active_season(db)
    now = activity_game.now_ms()
    if season is not None and not season.starts_ms <= now < season.ends_ms:
        season = None
    owner = None
    if row is not None and row.pet_id is not None:
        owner = {
            "pet_id": row.pet_id,
            "name": row.pet_name,
            "is_mine": row.app_user_id == viewer_id,
            "certification": row.certification,
            "occupied_at": row.occupied_at,
            "season_record": None,
        }
    result = {
        "site_id": site_id,
        "version": row.version if row else 0,
        "server_now_ms": now,
        "season_id": season.id if season else None,
        "status": "NO_ACTIVE_SEASON" if season is None else "UNOCCUPIED",
        "owner": owner,
    }
    if season is None or owner is None:
        return result
    account = await db.get(ActivityAccount, (season.id, row.pet_id))
    # Missing score is not a zero-point dog. Statistics may be pending independently:
    # the authoritative score already contains both points and the held-site count.
    if account is None:
        result["status"] = "PENDING"
        return result
    score = account.score
    owner["season_record"] = {
        "points": display_points(score),
        "owned_site_count": score["current_count"],
        "score_as_of_ms": score["last_ms"],
    }
    result["status"] = "READY"
    return result
