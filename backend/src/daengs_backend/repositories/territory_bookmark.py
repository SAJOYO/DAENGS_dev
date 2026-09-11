"""Saved-site SQL. The service holds the member lock for count + mutation."""

from sqlalchemy import delete, func, select

from daengs_backend.models.app_user import AppUser
from daengs_backend.models.territory_bookmark import TerritoryBookmark as Bookmark


async def active_member(db, owner):
    return (
        await db.scalar(select(AppUser.id).where(AppUser.id == owner, AppUser.status == "active"))
        is not None
    )


async def count(db, owner):
    return await db.scalar(
        select(func.count()).select_from(Bookmark).where(Bookmark.app_user_id == owner)
    )


async def saved_at(db, owner, site_id):
    return await db.scalar(
        select(Bookmark.created_at).where(
            Bookmark.app_user_id == owner, Bookmark.site_id == site_id
        )
    )


async def list_all(db, owner):
    return (
        await db.execute(
            select(Bookmark.site_id, Bookmark.created_at)
            .where(Bookmark.app_user_id == owner)
            .order_by(Bookmark.created_at.desc(), Bookmark.site_id)
        )
    ).all()


async def insert(db, owner, site_id):
    row = Bookmark(app_user_id=owner, site_id=site_id)
    db.add(row)
    await db.flush()
    return row.created_at


async def remove(db, owner, site_id):
    await db.execute(
        delete(Bookmark).where(Bookmark.app_user_id == owner, Bookmark.site_id == site_id)
    )
