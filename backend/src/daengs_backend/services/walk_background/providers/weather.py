"""Collect temperature through the existing Life adapter, outside diary generation."""

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
from functools import partial

from daengs_backend.orchestration.adapters.life import lookup_walk_weather
from daengs_backend.services.walk_background.contracts import Collected
from daengs_walk.value_contracts import Point
from daengs_walk.weather import GridTemperature

LOOKUP_TIMEOUT_S = 15
outcome = partial(Collected, provider="weather-observation", operation="/weather/at")


async def collect_temperature(content, point, *, lookup=None):
    try:
        target = datetime.fromisoformat(content["recorded_at"])
        location = Point.model_validate({"lat": point["lat"], "lng": point["lng"]})
        if target.tzinfo is None or target.utcoffset() is None:
            raise ValueError("missing timezone")
    except (ValueError, KeyError, TypeError):
        return outcome("not_requested", "invalid_weather_target")
    if target > datetime.now(UTC):
        return outcome("not_requested", "future_weather_target")
    if not (33 <= location.lat <= 39 and 124 <= location.lng <= 132):
        return outcome("not_requested", "outside_weather_coverage")
    try:
        result = await asyncio.wait_for(
            (lookup or lookup_walk_weather)(location.lat, location.lng, target),
            LOOKUP_TIMEOUT_S,
        )
    except TimeoutError:
        return outcome("unavailable", "weather_timeout", retryable=True)
    except Exception:  # noqa: BLE001 -- external enrichment must not crash the context worker
        return outcome("unavailable", "weather_lookup_failed", retryable=True)
    if result.status == "failed":
        return outcome("unavailable", "weather_lookup_failed", retryable=True)
    if result.status not in {"captured", "partial"} or result.temperature is None:
        return outcome("unavailable", "temperature_not_available")
    try:
        snapshot = GridTemperature(query_point=location, **asdict(result.temperature))
        if snapshot.requested_at != target:
            raise ValueError("another weather query")
    except (TypeError, ValueError):
        return outcome("unavailable", "invalid_weather_observation")
    return outcome(
        "known",
        payload=snapshot.model_dump(mode="json"),
        retrieved_at=snapshot.fetched_at.isoformat(),
        temporal_basis="source_observation",
    )
