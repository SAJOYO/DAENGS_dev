"""Life WeatherAt을 Walk Context 원자로 좁히는 경계."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from daengs_backend.orchestration.adapters import life as life_adapter
from daengs_life.app import deps
from daengs_life.realtime.cache import Cache, MemoryStore
from daengs_life.realtime.config import KST
from daengs_life.realtime.providers import kma_vilage_fcst

FIXTURES = Path(__file__).parents[1] / "fixtures" / "realtime"
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
