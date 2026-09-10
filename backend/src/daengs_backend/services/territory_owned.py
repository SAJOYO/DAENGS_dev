"""Member-owned current sites: one DB snapshot, then one bounded Place lookup."""

import base64
from datetime import UTC, datetime

from daengs_backend.repositories import activity as activity_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import territory_owned as repo
from daengs_backend.schemas.territory_owned import OwnedTerritoryCursor
from daengs_backend.services import activity, activity_game


class InvalidOwnedCursor(ValueError):
    pass


class OwnedSeasonChanged(ValueError):
    pass


def decode_cursor(value, owner, pet):
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        cursor = OwnedTerritoryCursor.model_validate_json(raw)
        if cursor.owner != owner or cursor.pet != pet:
            raise ValueError("cursor scope mismatch")
        return cursor
    except (ValueError, TypeError) as exc:
        raise InvalidOwnedCursor("invalid_cursor") from exc


def encode_cursor(owner, pet, season, after):
    value = OwnedTerritoryCursor(owner=owner, pet=pet, season=season, after=after)
    return base64.urlsafe_b64encode(value.model_dump_json().encode()).decode().rstrip("=")


async def list_owned(db, owner, lookup, *, pet_id=None, cursor=None, limit=50):
    activity.enabled()
    after = decode_cursor(cursor, owner, pet_id) if cursor else None
    if pet_id is not None and await pet_repo.get_owned(db, owner, pet_id) is None:
        raise activity.ActivityNotFound
    season = await activity_repo.read_active_season(db)
    now_ms = activity_game.now_ms()
    if season is not None and not season.starts_ms <= now_ms < season.ends_ms:
        season = None
    season_id = season.id if season else None
    if after is not None and after.season != season_id:
        raise OwnedSeasonChanged("season_changed")
    result = {
        "status": "READY" if season else "NO_ACTIVE_SEASON",
        "season_id": season_id,
        "server_now_ms": now_ms,
        "pet_id": pet_id,
        "total_count": 0,
        "items": [],
        "next_cursor": None,
    }
    if season is None:
        return result
    total, rows = await repo.page(
        db,
        owner,
        season_id,
        datetime.fromtimestamp(now_ms / 1000, UTC),
        pet_id,
        after.after if after else None,
        limit,
    )
    items = [dict(row._mapping) for row in rows[:limit]]
    # Release the read-only snapshot before remote I/O; counts and rows are materialized.
    await db.rollback()
    locations = await lookup.find_by_ids([row["site_id"] for row in items]) if items else {}
    for item in items:
        location = locations.get(item["site_id"])
        item["location"] = {"lat": location.lat, "lng": location.lng} if location else None
        item["location_status"] = "AVAILABLE" if location else "NOT_FOUND"
    result.update(total_count=total, items=items)
    if len(rows) > limit:
        result["next_cursor"] = encode_cursor(owner, pet_id, season_id, items[-1]["site_id"])
    return result
