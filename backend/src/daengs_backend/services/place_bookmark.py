"""Member saved records. Idempotent writes and a bounded complete list (no hidden page)."""

from daengs_backend.repositories import app_user as members
from daengs_backend.repositories import place_bookmark as repo
from daengs_backend.schemas.place_bookmark import (
    BookmarkItem,
    BookmarkKey,
    BookmarkList,
    BookmarkSearchResult,
)

BOOKMARK_LIMIT = 200


class BookmarkMemberNotActive(Exception):
    pass


class BookmarkLimitReached(Exception):
    pass


class BookmarkPlaceNotFound(Exception):
    pass


async def _page(db, owner):
    rows = await repo.list_all(db, owner)
    return BookmarkList(
        total_count=len(rows),
        limit=BOOKMARK_LIMIT,
        items=[
            BookmarkItem(
                key=BookmarkKey(source=row.source, ref=row.ref),
                name=row.name,
                created_at=row.created_at,
            )
            for row in rows
        ],
    )


async def list_saved(db, owner):
    if not await repo.active(db, owner):
        raise BookmarkMemberNotActive
    page = await _page(db, owner)
    await db.rollback()
    return page


async def search(db, owner, filters, lookup):
    page = await list_saved(db, owner)
    result = await lookup.lookup([item.key for item in page.items], filters)
    return BookmarkSearchResult(**page.model_dump(), **result)


async def _locked_page(db, owner):
    if await members.get_active_for_update(db, owner) is None:
        raise BookmarkMemberNotActive
    return await _page(db, owner)


async def save(db, owner, key, lookup):
    try:
        page = await _locked_page(db, owner)
        exists = any(item.key == key for item in page.items)
        await db.rollback()
        if exists:
            return page
        if page.total_count >= BOOKMARK_LIMIT:
            raise BookmarkLimitReached
        found = await lookup.lookup([key], {})
        if not found["hits"]:
            raise BookmarkPlaceNotFound
        name = found["hits"][0]["place"]["name"][:500]
        # Recheck capacity and withdrawal after I/O, without holding a DB lock during HTTP.
        page = await _locked_page(db, owner)
        if not any(item.key == key for item in page.items):
            if page.total_count >= BOOKMARK_LIMIT:
                raise BookmarkLimitReached
            await repo.insert(db, owner, key, name)
            page = await _page(db, owner)
        await db.commit()
        return page
    except BaseException:
        await db.rollback()
        raise


async def remove(db, owner, key):
    try:
        await _locked_page(db, owner)
        await repo.remove(db, owner, key)
        page = await _page(db, owner)
        await db.commit()
        return page
    except BaseException:
        await db.rollback()
        raise
