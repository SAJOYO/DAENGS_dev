"""Member bookmarks persist across dogs, ownership changes and seasons."""

from daengs_backend.repositories import app_user as members
from daengs_backend.repositories import territory_bookmark as repo
from daengs_backend.schemas.territory_bookmark import (
    TerritoryBookmarkItem,
    TerritoryBookmarkList,
    TerritoryBookmarkState,
)
from daengs_backend.schemas.territory_owned import OwnedTerritoryLocation
from daengs_backend.services.territory_site_lookup import TerritorySiteUnavailableError

BOOKMARK_LIMIT = 20


class BookmarkMemberNotActive(Exception):
    pass


class BookmarkLimitReached(Exception):
    def __init__(self, total_count):
        self.total_count = total_count


class BookmarkSiteNotFound(Exception):
    pass


def _state(site_id, created_at, total):
    return TerritoryBookmarkState(
        site_id=site_id,
        is_bookmarked=created_at is not None,
        created_at=created_at,
        total_count=total,
        limit=BOOKMARK_LIMIT,
    )


async def _locked_state(db, owner, site_id):
    # This row lock serializes saves, deletes and withdrawal for this member only.
    if await members.get_active_for_update(db, owner) is None:
        raise BookmarkMemberNotActive
    return _state(site_id, await repo.saved_at(db, owner, site_id), await repo.count(db, owner))


def _check_capacity(state):
    if not state.is_bookmarked and state.total_count >= BOOKMARK_LIMIT:
        raise BookmarkLimitReached(state.total_count)


async def save(db, owner, site_id, lookup):
    try:
        state = await _locked_state(db, owner, site_id)
        _check_capacity(state)
        await db.rollback()
        # An existing bookmark remains saved even if Place removed the site or is down.
        if state.is_bookmarked:
            return state
        sites = await lookup.find_by_ids([site_id])
        if site_id not in sites:
            raise BookmarkSiteNotFound
        # Recheck after I/O: another request may have filled the limit or withdrawn.
        state = await _locked_state(db, owner, site_id)
        _check_capacity(state)
        if not state.is_bookmarked:
            created = await repo.insert(db, owner, site_id)
            state = _state(site_id, created, state.total_count + 1)
        await db.commit()
        return state
    except BaseException:
        await db.rollback()
        raise


async def remove(db, owner, site_id):
    try:
        if await members.get_active_for_update(db, owner) is None:
            raise BookmarkMemberNotActive
        await repo.remove(db, owner, site_id)
        state = _state(site_id, None, await repo.count(db, owner))
        await db.commit()
        return state
    except BaseException:
        await db.rollback()
        raise


async def list_saved(db, owner, lookup):
    if not await repo.active_member(db, owner):
        raise BookmarkMemberNotActive
    # Materialize values in the same read-only snapshot as authentication.
    rows = [(row.site_id, row.created_at) for row in await repo.list_all(db, owner)]
    await db.rollback()
    locations = {}
    unavailable = False
    try:
        # Keep requests bounded even if an administrator lowers a future limit.
        for offset in range(0, len(rows), 100):
            locations.update(
                await lookup.find_by_ids([site for site, _ in rows[offset : offset + 100]])
            )
    except TerritorySiteUnavailableError:
        unavailable = True
    items = []
    for site_id, created in rows:
        location = locations.get(site_id)
        items.append(
            TerritoryBookmarkItem(
                site_id=site_id,
                created_at=created,
                location=OwnedTerritoryLocation(lat=location.lat, lng=location.lng)
                if location
                else None,
                location_status="AVAILABLE"
                if location
                else ("UNAVAILABLE" if unavailable else "NOT_FOUND"),
            )
        )
    return TerritoryBookmarkList(total_count=len(items), limit=BOOKMARK_LIMIT, items=items)
