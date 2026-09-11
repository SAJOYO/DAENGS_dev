"""Internal key lookup; nginx does not expose /internal routes."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.core.db import get_session
from daengs_place.place.bookmarks import BookmarkLookup, BookmarkLookupResult, lookup_bookmarks

router = APIRouter(prefix="/internal/place/bookmarks", tags=["place-bookmark-lookup"])


@router.post("/lookup", response_model=BookmarkLookupResult)
async def lookup(request: BookmarkLookup, db: Annotated[AsyncSession, Depends(get_session)]):
    return await lookup_bookmarks(db, request)
