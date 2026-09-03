"""과거 환경 조회 HTTP 경계. 조립과 provider 선택은 서비스 아래에 있다."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from daengs_life.app.deps import get_cache, get_now
from daengs_life.app.dto.weather import WeatherAtOut, WeatherAtRequest
from daengs_life.app.services import weather as service
from daengs_life.realtime.cache import Cache

router = APIRouter(prefix="/weather", tags=["weather"])


@router.post(
    "/at",
    response_model=WeatherAtOut,
    summary="좌표와 과거 시각의 환경 관측",
)
def post_weather_at(
    body: WeatherAtRequest,
    cache: Annotated[Cache, Depends(get_cache)],
    now: Annotated[datetime, Depends(get_now)],
) -> WeatherAtOut:
    try:
        return service.weather_at(body, now, cache=cache)
    except service.FutureWeatherAtRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


__all__ = ["router"]
