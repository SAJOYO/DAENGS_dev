"""좌표와 과거 시각으로 당시 환경 원자를 다시 읽는 Life capability.

현재 산책 적합도를 만드는 ``collect``와 목적이 다르다. 이 모듈은 미래 예보·대기질·특보를
조립하지 않고, 요청 시각 이하의 기상청 격자 실황 한 발표만 읽는다. 결과는 판단어로 접지
않으며 Walk가 후속 PR에서 동결할 수 있는 관측값과 출처 상태를 그대로 돌려준다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from .cache import Cache
from .config import KST
from .geo import Grid, LatLon, to_grid
from .observation import Measurement, Observations, Q, ResolvedLocation, Source
from .providers import kma_vilage_fcst
from .transport.base import Budget

WEATHER_QUANTITIES: tuple[Q, ...] = (
    Q.TEMP,
    Q.HUMIDITY,
    Q.PRECIP_KIND,
    Q.PRECIP_MM,
)


class WeatherAtStatus(StrEnum):
    CAPTURED = "captured"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    FAILED = "failed"


class HistoricalSourceOutcome(StrEnum):
    OK = "ok"
    NO_DATA = "no_data"
    ERROR = "error"


class FutureWeatherAtError(ValueError):
    """과거 관측 capability에 미래 시각이 들어왔다."""


@dataclass(frozen=True)
class HistoricalSourceResult:
    provider: Source
    outcome: HistoricalSourceOutcome
    reason: str | None = None
    calls: int = 0


@dataclass(frozen=True)
class WeatherAtResult:
    requested_at: datetime
    fetched_at: datetime
    grid: Grid
    status: WeatherAtStatus
    measurements: tuple[Measurement, ...]
    sources: tuple[HistoricalSourceResult, ...]


def observation_hour(requested_at: datetime, fetched_at: datetime) -> datetime:
    """요청 시각 이하이면서 조회 시점에 이미 공개된 가장 가까운 격자 실황 시각."""

    _require_timezone(requested_at)
    _require_timezone(fetched_at)
    requested = requested_at.astimezone(KST)
    fetched = fetched_at.astimezone(KST)
    if requested > fetched:
        raise FutureWeatherAtError("과거 환경 조회 시각은 현재보다 늦을 수 없습니다.")

    hour = requested.replace(minute=0, second=0, microsecond=0)
    # 초단기실황은 정시 관측이 :40에 제공된다. 방금 끝난 산책의 대표 시각이 아직 발표되지
    # 않았다면 존재하지 않는 값을 기다리거나 현재값으로 대신하지 않고 이전 발표를 쓴다.
    if fetched < hour + timedelta(minutes=40):
        hour -= timedelta(hours=1)
    return hour


def weather_at(
    point: LatLon,
    requested_at: datetime,
    *,
    fetched_at: datetime,
    cache: Cache,
    budget: Budget | None = None,
) -> WeatherAtResult:
    """한 지점·시각의 기상청 실황 원자를 반환한다."""

    hour = observation_hour(requested_at, fetched_at)
    grid = to_grid(point)
    lookup = f"{grid.nx},{grid.ny}:{hour:%Y%m%d%H%M}"
    got = cache.get_snapshot(
        Source.NCST.value,
        lookup,
        lambda: kma_vilage_fcst.raw_ncst_at(grid, hour, budget=budget),
        fetched_at,
    )

    if got.payload is None:
        outcome = (
            HistoricalSourceOutcome.NO_DATA
            if got.failure_kind == "nodata"
            else HistoricalSourceOutcome.ERROR
        )
        return WeatherAtResult(
            requested_at=requested_at,
            fetched_at=fetched_at,
            grid=grid,
            status=(
                WeatherAtStatus.UNKNOWN
                if outcome is HistoricalSourceOutcome.NO_DATA
                else WeatherAtStatus.FAILED
            ),
            measurements=(),
            sources=(HistoricalSourceResult(Source.NCST, outcome, got.reason, got.calls),),
        )

    try:
        parsed = kma_vilage_fcst.parse_ncst(got.payload)
    except Exception as exc:  # noqa: BLE001 - provider payload failure becomes a source result
        return WeatherAtResult(
            requested_at=requested_at,
            fetched_at=fetched_at,
            grid=grid,
            status=WeatherAtStatus.FAILED,
            measurements=(),
            sources=(
                HistoricalSourceResult(
                    Source.NCST,
                    HistoricalSourceOutcome.ERROR,
                    f"파싱 실패: {type(exc).__name__}: {exc}",
                    got.calls,
                ),
            ),
        )

    selected = _select(point, grid, requested_at, parsed)
    if not selected:
        status = WeatherAtStatus.UNKNOWN
        outcome = HistoricalSourceOutcome.NO_DATA
        reason = "요청 시각과 격자에 해당하는 지원 관측값이 없습니다."
    else:
        quantities = {item.quantity for item in selected}
        status = (
            WeatherAtStatus.CAPTURED
            if quantities == set(WEATHER_QUANTITIES)
            else WeatherAtStatus.PARTIAL
        )
        outcome = HistoricalSourceOutcome.OK
        reason = None
    return WeatherAtResult(
        requested_at=requested_at,
        fetched_at=fetched_at,
        grid=grid,
        status=status,
        measurements=selected,
        sources=(HistoricalSourceResult(Source.NCST, outcome, reason, got.calls),),
    )


def weather_at_many(
    requests: Iterable[tuple[LatLon, datetime]],
    *,
    fetched_at: datetime,
    cache: Cache,
    budget: Budget | None = None,
) -> tuple[WeatherAtResult, ...]:
    """여러 후보를 조회한다. 같은 격자·발표는 snapshot cache가 한 호출로 접는다."""

    return tuple(
        weather_at(
            point,
            requested_at,
            fetched_at=fetched_at,
            cache=cache,
            budget=budget,
        )
        for point, requested_at in requests
    )


def _select(
    point: LatLon,
    grid: Grid,
    requested_at: datetime,
    measurements: list[Measurement],
) -> tuple[Measurement, ...]:
    observations = Observations(
        location=ResolvedLocation(
            point=point,
            grid=grid,
            label=f"{point.lat:.4f}, {point.lon:.4f}",
        ),
        # latest()가 미래 발표를 집지 않도록 조회한 현재가 아니라 산책 시각을 기준으로 한다.
        fetched_at=requested_at,
        measurements=measurements,
    )
    return tuple(
        measurement
        for quantity in WEATHER_QUANTITIES
        if (measurement := observations.latest(quantity)) is not None
    )


def _require_timezone(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("환경 조회 시각은 시간대를 가져야 합니다.")


__all__ = [
    "WEATHER_QUANTITIES",
    "FutureWeatherAtError",
    "HistoricalSourceOutcome",
    "HistoricalSourceResult",
    "WeatherAtResult",
    "WeatherAtStatus",
    "observation_hour",
    "weather_at",
    "weather_at_many",
]
