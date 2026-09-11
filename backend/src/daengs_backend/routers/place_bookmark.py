"""Private member facilities. Removal remains possible when Place is unavailable."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session, get_snapshot_session
from daengs_backend.core.deps import CurrentAppMemberTokenOnly
from daengs_backend.schemas.place_bookmark import (
    BookmarkInterpret,
    BookmarkInterpretResult,
    BookmarkKey,
    BookmarkList,
    BookmarkSearch,
    BookmarkSearchResult,
)
from daengs_backend.services import place_bookmark as service
from daengs_backend.services.place_bookmark_lookup import (
    PlaceBookmarkInvalidFilters,
    PlaceLookupUnavailable,
    get_place_bookmark_lookup,
)

router = APIRouter(prefix="/app/places/bookmarks", tags=["place-bookmarks"])
Database = Annotated[AsyncSession, Depends(get_session)]
Snapshot = Annotated[AsyncSession, Depends(get_snapshot_session)]
Lookup = Annotated[object, Depends(get_place_bookmark_lookup)]


async def _call(operation):
    try:
        return await operation
    except service.BookmarkMemberNotActive:
        raise HTTPException(401, "다시 로그인해 주세요.") from None
    except service.BookmarkLimitReached:
        raise HTTPException(
            409, {"code": "place_bookmark_limit", "limit": service.BOOKMARK_LIMIT}
        ) from None
    except service.BookmarkPlaceNotFound:
        raise HTTPException(404, {"code": "place_not_found"}) from None
    except PlaceBookmarkInvalidFilters:
        raise HTTPException(422, {"code": "invalid_bookmark_filters"}) from None
    except PlaceLookupUnavailable:
        raise HTTPException(503, {"code": "place_lookup_unavailable"}) from None


@router.get("", response_model=BookmarkList)
async def list_saved(user: CurrentAppMemberTokenOnly, db: Snapshot):
    return await _call(service.list_saved(db, user.app_user_id))


@router.post("/search", response_model=BookmarkSearchResult)
async def search(
    request: BookmarkSearch, user: CurrentAppMemberTokenOnly, db: Snapshot, lookup: Lookup
):
    return await _call(service.search(db, user.app_user_id, request.filters, lookup))


@router.put("", response_model=BookmarkList)
async def save(key: BookmarkKey, user: CurrentAppMemberTokenOnly, db: Database, lookup: Lookup):
    return await _call(service.save(db, user.app_user_id, key, lookup))


@router.post("/interpret", response_model=BookmarkInterpretResult)
async def interpret(
    request: BookmarkInterpret, user: CurrentAppMemberTokenOnly, db: Snapshot, lookup: Lookup
):
    # Authenticate active membership; only filter meaning crosses the Place boundary.
    await _call(service.list_saved(db, user.app_user_id))
    return await _call(
        lookup.interpret(
            request.query,
            request.filters,
            **({"search_policy": request.search_policy} if request.search_policy else {}),
        )
    )


@router.delete("", response_model=BookmarkList)
async def remove(
    key: Annotated[BookmarkKey, Query()], user: CurrentAppMemberTokenOnly, db: Database
):
    return await _call(service.remove(db, user.app_user_id, key))
