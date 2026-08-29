"""웹과 Android가 함께 소비할 canonical Place 검색 API."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.place.search import PlaceSearchRequest, PlaceSearchResponse, search_place_groups

router = APIRouter(prefix="/v2/places", tags=["places-v2"])


@router.post("/search", response_model=PlaceSearchResponse)
async def search(
    request: PlaceSearchRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> PlaceSearchResponse:
    return await search_place_groups(db, request)
