"""Current-season public game reads; no settlement writes or private activity sources."""

import asyncio
import base64
from datetime import UTC, datetime

from daengs_backend.config import settings
from daengs_backend.core.storage import get_storage
from daengs_backend.repositories import activity as activity_repo
from daengs_backend.repositories import territory_game as repo
from daengs_backend.repositories import territory_owned as owned_repo
from daengs_backend.schemas.territory_game import LeaderboardCursor, PublicSitesCursor
from daengs_backend.services import activity, activity_game
from daengs_backend.services.activity_core.first_season_rewards import REWARD_VERSION
from daengs_backend.services.activity_core.game_policy import POINT_DENOMINATOR
from daengs_backend.services.pet import PET_PHOTO_BRIDGE_DOWNLOAD_PATH


class InvalidGameCursor(ValueError):
    pass


class GameReadChanged(ValueError):
    pass


class GamePhotoNotFound(LookupError):
    pass


class GameMemberNotActive(PermissionError):
    pass


async def require_member(db, member):
    # Read-only authentication in the same snapshot. No second request-lifetime
    # FOR UPDATE session remains open while fetching Place/photo data.
    if not await repo.active_member(db, member):
        raise GameMemberNotActive


def decode_cursor(value, schema):
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        return schema.model_validate_json(raw)
    except (ValueError, TypeError) as exc:
        raise InvalidGameCursor("invalid_cursor") from exc


def encode_cursor(cursor):
    return base64.urlsafe_b64encode(cursor.model_dump_json().encode()).decode().rstrip("=")


async def current_season(db):
    activity.enabled()
    season = await activity_repo.read_active_season(db)
    now = activity_game.now_ms()
    if season is not None and not season.starts_ms <= now < season.ends_ms:
        season = None
    return season, now


def season_view(season):
    if season is None:
        return None
    return {
        "id": season.id,
        "starts_ms": season.starts_ms,
        "ends_ms": season.ends_ms,
        "policy_version": season.rules["version"],
        "revision": season.revision,
        "score_as_of_ms": season.confirmed_ms,
    }


def rates(season):
    rules = activity_game.rules_type(season.rules)(**season.rules)
    first = rules.version == REWARD_VERSION
    return {
        "point_denominator": POINT_DENOMINATOR,
        "unverified_hourly": rules.unverified_hourly_points if first else 0,
        "scoring_hourly": rules.verified_hourly_points if first else rules.hourly_points,
        "extra_site_bps": 0 if first else rules.extra_site_bps,
        "maximum_bps": 10_000 if first else rules.maximum_bps,
    }


def points(units):
    whole, fraction = divmod(int(units) * 10 // POINT_DENOMINATOR, 10)
    return f"{whole}.{fraction}" if fraction else str(whole)


def pet_view(row, viewer):
    return {
        "pet_id": row.pet_id,
        "name": row.name,
        "breed": row.breed,
        "is_mine": row.app_user_id == viewer,
        "has_photo": row.has_photo,
        "photo_updated_at": row.photo_updated_at,
    }


def record_view(row, season, counts):
    owned, verified = counts.get(row.pet_id, (0, 0))
    return {
        "rank": row.rank,
        "points": points(row.total_units),
        "base_points": int(row.base_points) if row.base_points is not None else None,
        "takeover_points": int(row.takeover_points) if row.takeover_points is not None else None,
        "holding_points": points(row.holding_units),
        "owned_site_count": owned,
        "verified_site_count": verified,
        "score_as_of_ms": season.confirmed_ms,
    }


def check_season(after, season):
    if after and after.season != (season.id if season else None):
        raise GameReadChanged("season_changed")


async def leaderboard(db, viewer, *, cursor=None, limit=50):
    activity.enabled()
    after = decode_cursor(cursor, LeaderboardCursor) if cursor else None
    season, now = await current_season(db)
    check_season(after, season)
    if after and after.revision != season.revision:
        raise GameReadChanged("leaderboard_changed")
    result = {
        "status": "READY" if season else "NO_ACTIVE_SEASON",
        "server_now_ms": now,
        "season": season_view(season),
        "total_count": 0,
        "items": [],
        "next_cursor": None,
    }
    if season is None:
        return result
    total, rows = await repo.leaderboard(db, season, rates(season), after, limit)
    shown = rows[:limit]
    counts = await repo.owned_counts(
        db, season.id, datetime.fromtimestamp(now / 1000, UTC), [row.pet_id for row in shown]
    )
    result.update(
        total_count=total,
        items=[
            {"pet": pet_view(row, viewer), "season_record": record_view(row, season, counts)}
            for row in shown
        ],
    )
    if len(rows) > limit:
        last = shown[-1]
        result["next_cursor"] = encode_cursor(
            LeaderboardCursor(
                season=season.id,
                revision=season.revision,
                units=str(int(last.total_units)),
                pet=last.pet_id,
            )
        )
    return result


async def require_pet(db, pet_id, *, photo=False):
    pet = await repo.public_pet(db, pet_id, photo=photo)
    if pet is None:
        raise activity.ActivityNotFound
    return pet


async def profile(db, viewer, pet_id):
    activity.enabled()
    pet = await require_pet(db, pet_id)
    season, now = await current_season(db)
    result = {
        "status": "NOT_PARTICIPATING" if season else "NO_ACTIVE_SEASON",
        "server_now_ms": now,
        "season": season_view(season),
        "pet": pet_view(pet, viewer),
        "season_record": None,
    }
    if season is None:
        return result
    row = await repo.standing(db, season, rates(season), pet_id)
    if row is not None:
        counts = await repo.owned_counts(
            db, season.id, datetime.fromtimestamp(now / 1000, UTC), [pet_id]
        )
        result.update(status="READY", season_record=record_view(row, season, counts))
    return result


async def sites(db, pet_id, lookup, *, cursor=None, limit=50):
    activity.enabled()
    after = decode_cursor(cursor, PublicSitesCursor) if cursor else None
    if after and after.pet != pet_id:
        raise InvalidGameCursor("invalid_cursor")
    await require_pet(db, pet_id)
    season, now = await current_season(db)
    check_season(after, season)
    result = {
        "status": "READY" if season else "NO_ACTIVE_SEASON",
        "server_now_ms": now,
        "season_id": season.id if season else None,
        "pet_id": pet_id,
        "total_count": 0,
        "items": [],
        "next_cursor": None,
    }
    if season is None:
        return result
    total, rows = await owned_repo.page(
        db,
        None,
        season.id,
        datetime.fromtimestamp(now / 1000, UTC),
        pet_id,
        after.after if after else None,
        limit,
    )
    items = [dict(row._mapping) for row in rows[:limit]]
    if len(rows) > limit:
        result["next_cursor"] = encode_cursor(
            PublicSitesCursor(season=season.id, pet=pet_id, after=items[-1]["site_id"])
        )
    # No DB connection held during Place I/O; materialize before rollback expires ORM state.
    await db.rollback()
    locations = await lookup.find_by_ids([row["site_id"] for row in items]) if items else {}
    for item in items:
        location = locations.get(item["site_id"])
        item["location"] = {"lat": location.lat, "lng": location.lng} if location else None
        item["location_status"] = "AVAILABLE" if location else "NOT_FOUND"
    result.update(total_count=total, items=items)
    return result


async def photo(db, pet_id):
    activity.enabled()
    pet = await require_pet(db, pet_id, photo=True)
    if not pet.has_photo:
        raise GamePhotoNotFound
    # Only confirmed profile-photo metadata is selected; never attempts or walk photos.
    await db.rollback()
    url = await asyncio.to_thread(
        lambda: get_storage().download_url(
            pet.photo_storage_key,
            expires_in_seconds=settings.gait_download_url_ttl_seconds,
            generation=pet.photo_generation,
            bridge_download_path=PET_PHOTO_BRIDGE_DOWNLOAD_PATH,
        )
    )
    return {
        "download_url": url,
        "content_type": pet.photo_content_type,
        "size_bytes": pet.photo_size_bytes,
        "updated_at": pet.photo_updated_at,
    }
