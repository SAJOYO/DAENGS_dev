"""과거 환경 도메인을 HTTP 계약으로 옮기는 서비스 층."""

from __future__ import annotations

import math
from datetime import datetime

from daengs_life.app.dto.weather import (
    WeatherAtomOut,
    WeatherAtOut,
    WeatherAtRequest,
    WeatherSourceOut,
)
from daengs_life.realtime.cache import Cache
from daengs_life.realtime.geo import LatLon
from daengs_life.realtime.observation import Code, Interval, Measurement, Q
from daengs_life.realtime.weather_at import FutureWeatherAtError
from daengs_life.realtime.weather_at import weather_at as query_weather_at

UNITS: dict[Q, str] = {
    Q.TEMP: "celsius",
    Q.HUMIDITY: "percent",
    Q.PRECIP_MM: "millimeter",
}


class FutureWeatherAtRequestError(ValueError):
    """공개 요청이 과거 조회 범위를 벗어났다."""


def weather_at(body: WeatherAtRequest, fetched_at: datetime, *, cache: Cache) -> WeatherAtOut:
    try:
        result = query_weather_at(
            LatLon(body.lat, body.lon),
            body.observed_at,
            fetched_at=fetched_at,
            cache=cache,
        )
    except FutureWeatherAtError as exc:
        raise FutureWeatherAtRequestError(str(exc)) from exc
    return WeatherAtOut(
        status=result.status.value,
        requested_at=result.requested_at,
        fetched_at=result.fetched_at,
        grid=(result.grid.nx, result.grid.ny),
        observations=[_atom(item) for item in result.measurements],
        sources=[
            WeatherSourceOut(
                provider=item.provider.value,
                outcome=item.outcome.value,
                reason=item.reason,
                calls=item.calls,
            )
            for item in result.sources
        ],
    )


def _atom(item: Measurement) -> WeatherAtomOut:
    common = {
        "quantity": item.quantity.value,
        "unit": UNITS.get(item.quantity),
        "source": item.source.value,
        "spatial_ref": item.spatial_ref,
        "valid_at": item.valid_at,
        "issued_at": item.issued_at,
    }
    if isinstance(item.value, Code):
        return WeatherAtomOut(
            **common,
            representation="code",
            value=item.value.name,
            raw_value=item.value.raw,
        )
    if isinstance(item.value, Interval):
        return WeatherAtomOut(
            **common,
            representation="interval",
            value=item.value.label,
            lower_bound=item.value.lo,
            upper_bound=item.value.hi if math.isfinite(item.value.hi) else None,
        )
    return WeatherAtomOut(**common, representation="number", value=float(item.value))


__all__ = ["FutureWeatherAtRequestError", "weather_at"]
