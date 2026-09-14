"""Entry collection owns timing/provenance; a missing observation remains missing."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from daengs_backend.orchestration.adapters.life import (
    WalkTemperatureObservation,
    WalkWeatherObservation,
)
from daengs_backend.services import walk_entry_context as worker
from daengs_backend.services import walk_entry_context_source as source
from daengs_backend.services.walk_background.providers import weather
from tests.walk.support.entry_context import CONTENT, NOW


def observation():
    at = NOW.replace(minute=0, second=0, microsecond=0)
    return WalkWeatherObservation(
        status="partial",  # Other quantities missing does not invalidate temperature.
        temperature=WalkTemperatureObservation(
            requested_at=NOW,
            fetched_at=NOW,
            grid=(61, 125),
            observed_at=at,
            issued_at=at,
            temperature_c=22.5,
            provider="kma-vilage-fcst:ncst",
        ),
    )


async def test_worker_preserves_observation_identity_outside_transaction(state, monkeypatch):
    state.job.tag = state.ticket["tag"] = "environment.weather"

    async def lookup(lat, lon, at):
        assert not state.active[0]
        assert (lat, lon, at) == (37.5, 127, NOW)
        return observation()

    monkeypatch.setattr(weather, "lookup_walk_weather", lookup)
    assert await worker.process(state.factory, limit=1) == 1
    saved = state.added[0].envelope
    assert saved["provenance"]["temporal_basis"] == "source_observation"
    assert saved["provenance"]["provider"] == "weather-observation"
    assert saved["payload"]["provider"] == "kma-vilage-fcst:ncst"
    assert saved["payload_sha256"] == source.digest(saved["payload"])
    assert saved["payload"]["temperature_c"] == 22.5
    assert "area_radius_m" not in saved["payload"] and "valid_until" not in saved["payload"]
    assert state.record.payload == CONTENT


@pytest.mark.parametrize("status,retry", [("unknown", False), ("partial", False), ("failed", True)])
async def test_unavailable_temperature_never_turns_into_zero_or_current_weather(status, retry):
    lookup = AsyncMock(return_value=WalkWeatherObservation(status=status))
    got = await weather.collect_temperature(CONTENT, CONTENT["location"], lookup=lookup)
    assert got.status == "unavailable" and got.payload is None
    assert got.retryable is retry
    lookup.assert_awaited_once_with(37.5, 127, NOW)


async def test_wrong_timestamp_is_rejected():
    value = observation()
    value = replace(
        value, temperature=replace(value.temperature, requested_at=NOW - timedelta(seconds=1))
    )
    got = await weather.collect_temperature(
        CONTENT, CONTENT["location"], lookup=AsyncMock(return_value=value)
    )
    assert got.reason == "invalid_weather_observation" and got.payload is None


async def test_terminal_pin_point_is_the_weather_query_and_provisional_pin_does_not_query(
    monkeypatch,
):
    lookup = AsyncMock(return_value=observation())
    monkeypatch.setattr(weather, "lookup_walk_weather", lookup)
    content = {**CONTENT, "pin": {"state": "resolved", "point": {"lat": 37.6, "lng": 127.1}}}
    got = await source.collect("environment.weather", content)
    lookup.assert_awaited_once_with(37.6, 127.1, NOW)
    assert got.payload["query_point"] == content["pin"]["point"]
    content["pin"]["state"] = "provisional"
    assert (await source.collect("environment.weather", content)).reason == "pin_not_final"
    assert lookup.await_count == 1


async def test_timeout_and_exception_are_bounded_and_do_not_copy_secret(monkeypatch):
    monkeypatch.setattr(weather, "LOOKUP_TIMEOUT_S", 0.001)

    async def wait(*_):
        await asyncio.Event().wait()

    got = await weather.collect_temperature(CONTENT, CONTENT["location"], lookup=wait)
    assert got.reason == "weather_timeout" and got.retryable
    got = await weather.collect_temperature(
        CONTENT, CONTENT["location"], lookup=AsyncMock(side_effect=RuntimeError("secret"))
    )
    assert got.reason == "weather_lookup_failed" and got.retryable
    assert "secret" not in str(got)


@pytest.mark.parametrize("change", ["future", "timezone", "coverage"])
async def test_unsupported_query_does_not_call_life(change):
    content, point = dict(CONTENT), dict(CONTENT["location"])
    if change == "future":
        content["recorded_at"] = (NOW + timedelta(days=1)).isoformat()
    elif change == "timezone":
        content["recorded_at"] = NOW.replace(tzinfo=None).isoformat()
    else:
        point["lat"] = 0
    lookup = AsyncMock()
    got = await weather.collect_temperature(content, point, lookup=lookup)
    assert got.status == "not_requested"
    lookup.assert_not_awaited()
