"""Member-scoped SQL. Caller serializes writes using the active member row lock."""

from sqlalchemy import delete, select

from daengs_backend.models.app_user import AppUser
from daengs_backend.models.place_bookmark import PlaceBookmark


async def active(db, owner):
    return (
        await db.scalar(select(AppUser.id).where(AppUser.id == owner, AppUser.status == "active"))
        is not None
    )


async def list_all(db, owner):
    return (
        await db.execute(
            select(
                PlaceBookmark.source,
                PlaceBookmark.ref,
                PlaceBookmark.name,
                PlaceBookmark.created_at,
            )
            .where(PlaceBookmark.app_user_id == owner)
            .order_by(PlaceBookmark.created_at.desc(), PlaceBookmark.source, PlaceBookmark.ref)
        )
    ).all()


async def insert(db, owner, key, name):
    db.add(PlaceBookmark(app_user_id=owner, source=key.source, ref=key.ref, name=name))
    await db.flush()


async def remove(db, owner, key):
    await db.execute(
        delete(PlaceBookmark).where(
            PlaceBookmark.app_user_id == owner,
            PlaceBookmark.source == key.source,
            PlaceBookmark.ref == key.ref,
        )
    )
