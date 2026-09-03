"""좌표·과거 시각 환경 조회의 시간 선택, 저하, 캐시 계약."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from daengs_life.realtime.cache import Cache, MemoryStore
from daengs_life.realtime.config import KST
from daengs_life.realtime.geo import LatLon
from daengs_life.realtime.observation import Q
from daengs_life.realtime.providers import kma_vilage_fcst
from daengs_life.realtime.transport.base import NoData, Unavailable
from daengs_life.realtime.weather_at import (
    FutureWeatherAtError,
    HistoricalSourceOutcome,
    WeatherAtStatus,
    observation_hour,
    weather_at,
    weather_at_many,
)

FIXTURES = Path(__file__).parent / "fixtures" / "realtime"
HERE = LatLon(37.4979, 127.0276)
NOW = datetime(2026, 8, 25, 10, 10, tzinfo=KST)


def ncst() -> dict:
    payload = json.loads((FIXTURES / "kma-vilage-fcst.ncst.json").read_text(encoding="utf-8"))
    return payload["response"]["body"]


def wire(monkeypatch: pytest.MonkeyPatch, payload: dict | Exception) -> list[datetime]:
    calls: list[datetime] = []

    def raw(_grid, at, *, budget=None):
        calls.append(at)
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr(kma_vilage_fcst, "raw_ncst_at", raw)
    return calls


def test_the_latest_published_hour_is_chosen_without_moving_the_walk_time() -> None:
    requested = datetime(2026, 8, 25, 9, 20, tzinfo=KST)
    assert observation_hour(requested, NOW) == datetime(2026, 8, 25, 9, tzinfo=KST)

    # 10시 관측은 10:40에 공개된다. 종료 직후에는 존재하지 않는 회차를 묻지 않는다.
    just_finished = datetime(2026, 8, 25, 10, 5, tzinfo=KST)
    assert observation_hour(just_finished, NOW) == datetime(2026, 8, 25, 9, tzinfo=KST)


def test_a_complete_nowcast_becomes_four_provenanced_atoms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = wire(monkeypatch, ncst())
    requested = datetime(2026, 8, 25, 9, 20, tzinfo=KST)

    got = weather_at(HERE, requested, fetched_at=NOW, cache=Cache(MemoryStore()))

    assert got.status is WeatherAtStatus.CAPTURED
    assert {item.quantity for item in got.measurements} == {
        Q.TEMP,
        Q.HUMIDITY,
        Q.PRECIP_KIND,
        Q.PRECIP_MM,
    }
    assert {item.valid_at for item in got.measurements} == {
        datetime(2026, 8, 25, 9, tzinfo=KST)
    }
    assert got.requested_at == requested and got.fetched_at == NOW
    assert got.sources[0].outcome is HistoricalSourceOutcome.OK
    assert calls == [datetime(2026, 8, 25, 9, tzinfo=KST)]


def test_same_grid_and_hour_share_one_transport_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = wire(monkeypatch, ncst())
    requests = (
        (HERE, datetime(2026, 8, 25, 9, 5, tzinfo=KST)),
        (HERE, datetime(2026, 8, 25, 9, 50, tzinfo=KST)),
    )

    got = weather_at_many(requests, fetched_at=NOW, cache=Cache(MemoryStore()))

    assert len(got) == 2
    assert calls == [datetime(2026, 8, 25, 9, tzinfo=KST)]
    assert got[0].sources[0].calls == 1
    assert got[1].sources[0].calls == 0


def test_missing_one_supported_quantity_is_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = copy.deepcopy(ncst())
    payload["items"]["item"] = [
        item for item in payload["items"]["item"] if item["category"] != "RN1"
    ]
    wire(monkeypatch, payload)

    got = weather_at(
        HERE,
        datetime(2026, 8, 25, 9, 20, tzinfo=KST),
        fetched_at=NOW,
        cache=Cache(MemoryStore()),
    )

    assert got.status is WeatherAtStatus.PARTIAL
    assert Q.PRECIP_MM not in {item.quantity for item in got.measurements}
    assert got.sources[0].outcome is HistoricalSourceOutcome.OK


@pytest.mark.parametrize(
    ("failure", "status", "outcome"),
    [
        (NoData("그 시각 자료 없음"), WeatherAtStatus.UNKNOWN, HistoricalSourceOutcome.NO_DATA),
        (Unavailable("게이트웨이 장애"), WeatherAtStatus.FAILED, HistoricalSourceOutcome.ERROR),
    ],
)
def test_transport_outcomes_stay_distinguishable(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    status: WeatherAtStatus,
    outcome: HistoricalSourceOutcome,
) -> None:
    wire(monkeypatch, failure)

    got = weather_at(
        HERE,
        datetime(2026, 8, 25, 9, 20, tzinfo=KST),
        fetched_at=NOW,
        cache=Cache(MemoryStore()),
    )

    assert got.status is status
    assert got.measurements == ()
    assert got.sources[0].outcome is outcome
    assert got.sources[0].reason


def test_no_supported_values_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    wire(monkeypatch, {"items": {"item": []}})

    got = weather_at(
        HERE,
        datetime(2026, 8, 25, 9, 20, tzinfo=KST),
        fetched_at=NOW,
        cache=Cache(MemoryStore()),
    )

    assert got.status is WeatherAtStatus.UNKNOWN
    assert got.sources[0].outcome is HistoricalSourceOutcome.NO_DATA


def test_a_malformed_provider_value_is_a_failed_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = copy.deepcopy(ncst())
    precip = next(item for item in payload["items"]["item"] if item["category"] == "PTY")
    precip["obsrValue"] = "not-a-code"
    wire(monkeypatch, payload)

    got = weather_at(
        HERE,
        datetime(2026, 8, 25, 9, 20, tzinfo=KST),
        fetched_at=NOW,
        cache=Cache(MemoryStore()),
    )

    assert got.status is WeatherAtStatus.FAILED
    assert got.sources[0].outcome is HistoricalSourceOutcome.ERROR
    assert "파싱 실패" in (got.sources[0].reason or "")


def test_future_and_naive_times_fail_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = wire(monkeypatch, ncst())
    cache = Cache(MemoryStore())

    with pytest.raises(FutureWeatherAtError):
        weather_at(
            HERE,
            datetime(2026, 8, 25, 10, 11, tzinfo=KST),
            fetched_at=NOW,
            cache=cache,
        )
    with pytest.raises(ValueError, match="시간대"):
        naive = datetime(2026, 8, 25, 9, 20, tzinfo=KST).replace(tzinfo=None)
        weather_at(HERE, naive, fetched_at=NOW, cache=cache)

    assert calls == []
