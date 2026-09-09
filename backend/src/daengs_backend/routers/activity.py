"""Authenticated reads over server-owned activity sources."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.schemas.activity import (
    SessionLinkResponse,
    TerritorySummaryResponse,
    WalkSummaryResponse,
    WalkWindow,
)
from daengs_backend.services import activity as service

router = APIRouter(prefix="/app/activity", tags=["activity"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/seasons/current")
async def current_season(user: CurrentAppUser, db: Session):
    return await _call(service.current_season(db))


async def _call(operation):
    try:
        return await operation
    except service.ActivityNotFound:
        raise HTTPException(404, {"code": "activity_not_found"}) from None
    except service.ActivityDisabled:
        raise HTTPException(503, {"code": "activity_disabled"}) from None


@router.get("/sessions/{client_session_id}", response_model=SessionLinkResponse)
async def session_link(client_session_id: uuid.UUID, user: CurrentAppUser, db: Session):
    return await _call(service.links(db, user.app_user_id, client_session_id))


@router.get("/walks/summary", response_model=WalkSummaryResponse)
async def walk_summary(user: CurrentAppUser, db: Session, window: Annotated[WalkWindow, Query()]):
    return await _call(
        service.walk_summary(db, user.app_user_id, window.from_ms, window.to_ms, window.pet_id)
    )


@router.get("/territory/{season_id}/pets/{pet_id}", response_model=TerritorySummaryResponse)
async def territory_summary(season_id: str, pet_id: uuid.UUID, user: CurrentAppUser, db: Session):
    return await _call(service.territory_summary(db, user.app_user_id, season_id, pet_id))
