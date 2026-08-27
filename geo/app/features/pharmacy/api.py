"""GET /pharmacy/search — 약국. 얇다.

약국은 대개 사람 혼자 간다 (처방전·약 수령). 그래서 companion 기본 none, 대화 루프 없음, 영업중·거리만.
경로가 필요하면 /journey(companion=none)로.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.geo.schemas import SearchOut, SearchParams
from app.geo.search import search_places

router = APIRouter(prefix="/pharmacy", tags=["pharmacy"])


@router.get("/search", response_model=SearchOut)
async def pharmacy_search(
    params: Annotated[SearchParams, Query()],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SearchOut:
    params = params.model_copy(update={"kind": "pharmacy"})
    return await search_places(db, params)
