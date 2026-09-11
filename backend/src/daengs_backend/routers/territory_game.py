"""Public game information is shared among authenticated app members."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_snapshot_session
from daengs_backend.core.deps import AppPrincipal, CurrentAppMemberTokenOnly
from daengs_backend.core.storage import StorageNotConfiguredError
from daengs_backend.schemas.pet import PetPhotoResponse
from daengs_backend.schemas.territory_game import GameLeaderboard, GameProfile
from daengs_backend.schemas.territory_owned import OwnedTerritoryPage
from daengs_backend.services import activity
from daengs_backend.services import territory_game as service
from daengs_backend.services.territory_site_batch_lookup import (
    TerritorySiteBatchLookup,
    get_territory_site_batch_lookup,
)
from daengs_backend.services.territory_site_lookup import TerritorySiteUnavailableError

router = APIRouter(prefix="/app/territory", tags=["territory-game"])
Snapshot = Annotated[AsyncSession, Depends(get_snapshot_session)]
Cursor = Annotated[str | None, Query(min_length=1, max_length=1024)]
Limit = Annotated[int, Query(ge=1, le=100)]


async def game_member(user: CurrentAppMemberTokenOnly, db: Snapshot):
    await _call(service.require_member(db, user.app_user_id))
    return user


GameMember = Annotated[AppPrincipal, Depends(game_member)]


async def _call(operation):
    try:
        return await operation
    except service.GameMemberNotActive:
        raise HTTPException(401, "다시 로그인해 주세요.") from None
    except activity.ActivityDisabled:
        raise HTTPException(503, {"code": "activity_disabled"}) from None
    except activity.ActivityNotFound:
        raise HTTPException(404, {"code": "pet_not_found"}) from None
    except service.GamePhotoNotFound:
        raise HTTPException(404, {"code": "no_photo"}) from None
    except service.InvalidGameCursor:
        raise HTTPException(400, {"code": "invalid_cursor"}) from None
    except service.GameReadChanged as exc:
        raise HTTPException(409, {"code": str(exc)}) from None
    except TerritorySiteUnavailableError:
        raise HTTPException(503, {"code": "territory_sites_unavailable"}) from None
    except StorageNotConfiguredError:
        raise HTTPException(503, {"code": "photo_unavailable"}) from None


@router.get("/leaderboard", response_model=GameLeaderboard)
async def leaderboard(user: GameMember, db: Snapshot, cursor: Cursor = None, limit: Limit = 50):
    return await _call(service.leaderboard(db, user.app_user_id, cursor=cursor, limit=limit))


@router.get("/pets/{pet_id}/profile", response_model=GameProfile)
async def profile(pet_id: uuid.UUID, user: GameMember, db: Snapshot):
    return await _call(service.profile(db, user.app_user_id, pet_id))


@router.get("/pets/{pet_id}/sites", response_model=OwnedTerritoryPage)
async def sites(
    pet_id: uuid.UUID,
    user: GameMember,
    db: Snapshot,
    lookup: Annotated[TerritorySiteBatchLookup, Depends(get_territory_site_batch_lookup)],
    cursor: Cursor = None,
    limit: Limit = 50,
):
    return await _call(service.sites(db, pet_id, lookup, cursor=cursor, limit=limit))


@router.get("/pets/{pet_id}/photo", response_model=PetPhotoResponse)
async def photo(pet_id: uuid.UUID, user: GameMember, db: Snapshot):
    return await _call(service.photo(db, pet_id))
