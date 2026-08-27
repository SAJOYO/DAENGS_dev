"""기존 `GET /facility/search` HTTP 표면.

검색 SQL과 병합 규칙은 Place 내부 resolver가 소유한다. 이 모듈의 re-export는 기존 호출자와
테스트의 import 계약을 유지하기 위한 migration bridge다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.place.facility_resolver import (
    _SEARCH,
    MEDICAL,
    FacilityOut,
    FacilityParams,
    FacilitySearchOut,
    FacilitySourceOut,
    PetAxesOut,
    _merge,
    resolve_facilities,
)
from app.place.facility_resolver import (
    MAX_RESULTS as _DEFAULT_MAX_RESULTS,
)

router = APIRouter(prefix="/facility", tags=["facility"])

# 기존 테스트와 운영 튜닝 지점이 이 모듈의 상한을 참조한다. resolver에는 명시적으로 넘겨
# API 계층의 mutable 설정이 Place 도메인 안으로 새지 않게 한다.
MAX_RESULTS = _DEFAULT_MAX_RESULTS


@router.get("/search", response_model=FacilitySearchOut)
async def facility_search(
    params: Annotated[FacilityParams, Query()],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> FacilitySearchOut:
    return await resolve_facilities(params, db, max_results=MAX_RESULTS)


__all__ = (
    "MAX_RESULTS",
    "MEDICAL",
    "_SEARCH",
    "FacilityOut",
    "FacilityParams",
    "FacilitySearchOut",
    "FacilitySourceOut",
    "PetAxesOut",
    "_merge",
    "facility_search",
    "router",
)
