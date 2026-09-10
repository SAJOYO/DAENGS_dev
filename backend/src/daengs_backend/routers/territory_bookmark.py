"""Private saved sites; token validation never holds a second member transaction."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session, get_snapshot_session
from daengs_backend.core.deps import CurrentAppMemberTokenOnly
from daengs_backend.schemas.territory_bookmark import TerritoryBookmarkList, TerritoryBookmarkState
from daengs_backend.schemas.territory_claim import SiteId
from daengs_backend.services import territory_bookmark as service
from daengs_backend.services.territory_site_batch_lookup import (
    TerritorySiteBatchLookup,
    get_territory_site_batch_lookup,
)
from daengs_backend.services.territory_site_lookup import TerritorySiteUnavailableError

router = APIRouter(prefix="/app/territory/bookmarks", tags=["territory-bookmarks"])
Database = Annotated[AsyncSession, Depends(get_session)]
Snapshot = Annotated[AsyncSession, Depends(get_snapshot_session)]
Lookup = Annotated[TerritorySiteBatchLookup, Depends(get_territory_site_batch_lookup)]


async def _call(operation):
    try:
        return await operation
    except service.BookmarkMemberNotActive:
        raise HTTPException(401, "다시 로그인해 주세요.") from None
    except service.BookmarkLimitReached as exc:
        raise HTTPException(
            409,
            {
                "code": "bookmark_limit_reached",
                "limit": service.BOOKMARK_LIMIT,
                "total_count": exc.total_count,
            },
        ) from None
    except service.BookmarkSiteNotFound:
        raise HTTPException(404, {"code": "territory_site_not_found"}) from None
    except TerritorySiteUnavailableError:
        raise HTTPException(503, {"code": "territory_sites_unavailable"}) from None


@router.get("", response_model=TerritoryBookmarkList)
async def list_saved(user: CurrentAppMemberTokenOnly, db: Snapshot, lookup: Lookup):
    return await _call(service.list_saved(db, user.app_user_id, lookup))


@router.put("/{site_id}", response_model=TerritoryBookmarkState)
async def save(site_id: SiteId, user: CurrentAppMemberTokenOnly, db: Database, lookup: Lookup):
    return await _call(service.save(db, user.app_user_id, site_id, lookup))


@router.delete("/{site_id}", response_model=TerritoryBookmarkState)
async def remove(site_id: SiteId, user: CurrentAppMemberTokenOnly, db: Database):
    return await _call(service.remove(db, user.app_user_id, site_id))
