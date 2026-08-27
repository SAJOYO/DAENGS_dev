"""메뉴 진입용 검색 API. 챗봇 진입은 parse() → 같은 search_places()로 들어온다 (추후)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.geo.schemas import SearchOut, SearchParams
from app.geo.search import search_places

router = APIRouter(prefix="/places", tags=["places"])


@router.get("/search", response_model=SearchOut)
async def search(
    params: Annotated[SearchParams, Query()],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SearchOut:
    return await search_places(db, params)
