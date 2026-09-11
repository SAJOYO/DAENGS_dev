"""Life WeatherAt을 Walk Context 원자로 좁히는 경계."""

from __future__ import annotations

import copy
import json
from datetime import datetime

import pytest

from daengs_backend.orchestration.adapters import life as life_adapter
from daengs_life.app import deps
from daengs_life.realtime.cache import POLICY, Cache, MemoryStore
from daengs_life.realtime.config import KST
from daengs_life.realtime.providers import kma_vilage_fcst
from tests.walk.support.paths import TESTS

TEST_FIXTURES = TESTS / "fixtures"

FIXTURES = TEST_FIXTURES / "realtime"
NOW = datetime(2026, 8, 25, 10, 10, tzinfo=KST)
OBSERVED_AT = datetime(2026, 8, 25, 9, 20, tzinfo=KST)


def ncst() -> dict:
    payload = json.loads((FIXTURES / "kma-vilage-fcst.ncst.json").read_text(encoding="utf-8"))
    return payload["response"]["body"]


def wire(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict,
) -> None:
    monkeypatch.setattr(kma_vilage_fcst, "raw_ncst_at", lambda *_a, **_k: payload)
    monkeypatch.setattr(deps, "get_now", lambda: NOW)
    monkeypatch.setattr(deps, "get_cache", lambda: Cache(MemoryStore()))


def test_life_weather는_관측_원자와_출처를_Walk_계약으로_좁힌다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(monkeypatch, ncst())

    got = life_adapter._weather_at_life(37.4979, 127.0276, OBSERVED_AT)

    assert got.status == "captured"
    assert got.provider == "kma-vilage-fcst:ncst"
    assert got.observed_at == datetime(2026, 8, 25, 9, tzinfo=KST)
    assert got.temperature_c is not None
    assert got.humidity_pct is not None
    assert got.precipitation_kind == "none"
    assert got.precipitation_mm == 0
    assert got.temperature.grid == (61, 125)
    assert got.temperature.requested_at == OBSERVED_AT
    assert got.temperature.fetched_at == NOW
    assert got.temperature.observed_at == got.observed_at
    assert got.temperature.issued_at == got.observed_at


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", "rain"), ("2", "mixed"), ("3", "snow"), ("4", "rain")],
)
def test_kma_강수_코드는_WMO와_섞이지_않고_공통_의미로_좁힌다(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: str,
) -> None:
    payload = copy.deepcopy(ncst())
    item = next(row for row in payload["items"]["item"] if row["category"] == "PTY")
    item["obsrValue"] = raw
    wire(monkeypatch, payload)

    got = life_adapter._weather_at_life(37.4979, 127.0276, OBSERVED_AT)

    assert got.precipitation_kind == expected


@pytest.mark.asyncio
async def test_adapter_예외는_finalize가_소비할_실패_값이_된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args):
        raise RuntimeError("secret upstream detail")

    monkeypatch.setattr(life_adapter, "_weather_at_life", fail)

    got = await life_adapter.lookup_walk_weather(37.5, 127.0, OBSERVED_AT)

    assert got.status == "failed"
    assert got.failure_reason == "Life 과거 날씨 조회 실패: RuntimeError"
    assert "secret" not in got.failure_reason


def test_계약_범위를_벗어난_수치는_Walk_snapshot으로_새지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = copy.deepcopy(ncst())
    for item in payload["items"]["item"]:
        if item["category"] == "T1H":
            item["obsrValue"] = "999"
        elif item["category"] == "REH":
            item["obsrValue"] = "101"
        elif item["category"] == "RN1":
            item["obsrValue"] = "-1"
    wire(monkeypatch, payload)

    got = life_adapter._weather_at_life(37.4979, 127.0276, OBSERVED_AT)

    assert got.temperature_c is None
    assert got.humidity_pct is None
    assert got.precipitation_mm is None


def test_실시간_예약분에_닿으면_Walk는_외부_호출_없이_실패로_저하한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore()
    cache = Cache(store)
    feed = POLICY.feeds["kma-vilage-fcst:ncst"]
    limit = POLICY.budgets[feed.budget]
    assert limit is not None
    for _ in range(limit - POLICY.snapshot_live_reserve_calls):
        store.spend(feed.budget, "20260825")
    calls: list[int] = []
    monkeypatch.setattr(
        kma_vilage_fcst,
        "raw_ncst_at",
        lambda *_a, **_k: calls.append(1) or ncst(),
    )
    monkeypatch.setattr(deps, "get_now", lambda: NOW)
    monkeypatch.setattr(deps, "get_cache", lambda: cache)

    got = life_adapter._weather_at_life(37.4979, 127.0276, OBSERVED_AT)

    assert got.status == "failed"
    assert got.failure_reason is not None and "예약분" in got.failure_reason
    assert calls == []


@pytest.mark.parametrize(
    "change", [None, "grid", "time", "source", "unit", "forecast", "duplicate", "missing"]
)
def test_temperature_snapshot_is_identical_across_local_and_remote_and_rejects_mismatch(
    monkeypatch, change
):
    from daengs_backend.config import settings
    from daengs_backend.services import realtime_client
    from daengs_life.app.dto.weather import WeatherAtRequest
    from daengs_life.app.services.weather import weather_at

    wire(monkeypatch, ncst())
    local = life_adapter._weather_at_life(37.4979, 127.0276, OBSERVED_AT)
    result = weather_at(
        WeatherAtRequest(lat=37.4979, lon=127.0276, observed_at=OBSERVED_AT),
        NOW,
        cache=Cache(MemoryStore()),
    ).model_dump(mode="json")
    atom = next(a for a in result["observations"] if a["quantity"] == "temp_c")
    if change == "grid":
        result["grid"] = [60, 127]
    elif change == "time":
        result["requested_at"] = NOW.isoformat()
    elif change == "source":
        atom["source"] = "forecast"
    elif change == "unit":
        atom["unit"] = "fahrenheit"
    elif change == "forecast":
        atom["valid_at"] = NOW.isoformat()
    elif change == "duplicate":
        result["observations"].append(dict(atom))
    elif change == "missing":
        result["observations"].remove(atom)
    monkeypatch.setattr(settings, "realtime_url", "https://rt.example")
    monkeypatch.setattr(realtime_client, "post_weather_at", lambda *_a, **_k: (200, result))
    remote = life_adapter._weather_at_life(37.4979, 127.0276, OBSERVED_AT)
    if change is None:
        assert remote.temperature == local.temperature
    else:
        assert remote.temperature is None
