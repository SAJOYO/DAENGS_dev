"""Life boundary translation without copying retrieval or generation policy."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from datetime import datetime
from typing import Any, Literal, Protocol

from fastapi import HTTPException

from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    ErrorDetail,
    LifePayload,
    OutcomeDetail,
    WalkPayload,
)

WalkPrecipitationKind = Literal["none", "rain", "snow", "mixed"]
WalkWeatherStatus = Literal["captured", "partial", "unknown", "failed"]


@dataclass(frozen=True)
class WalkWeatherObservation:
    """Life 내부 계약을 Walk가 소비할 수 있는 원시 환경값으로 좁힌다."""

    status: WalkWeatherStatus
    provider: str | None = None
    observed_at: datetime | None = None
    temperature_c: float | None = None
    humidity_pct: float | None = None
    precipitation_kind: WalkPrecipitationKind | None = None
    precipitation_mm: float | None = None
    failure_reason: str | None = None


class WalkWeatherLookup(Protocol):
    async def __call__(
        self,
        lat: float,
        lon: float,
        observed_at: datetime,
    ) -> WalkWeatherObservation: ...


def _ask_life(
    question: str,
    *,
    breed: str | None = None,
    age_months: int | None = None,
    screening_verdict: str | None = None,
    screening_days_ago: int | None = None,
    screening_history: tuple[tuple[str, int], ...] = (),
) -> Any:
    """Open the existing request-scoped dependencies around the Life service shim.

    The dog facts cross as primitives, not as ``DogContext``: importing the contract type
    into ``daengs_life`` would make the domain depend on the orchestration layer, which is
    the direction D-035 forbids. Life decides what they mean.

    The screening verdict crosses the same way and for the same reason (#283) — two
    primitives, never ``ScreeningContext``. Both are ``None`` on every request that did not
    come from a screening result, and Life's prompt is then byte-identical to before.

    Earlier screenings cross as a tuple of ``(verdict, days_ago)`` pairs for that same reason
    (#79 3번): pairs of primitives, never ``ScreeningHistory``. Empty on every request that
    did not come from a screening result, and empty again for a dog's first record.
    """
    from daengs_life.app import deps
    from daengs_life.app.services import ask as life_service

    encoder = deps.get_encoder()
    connection_dependency = deps.get_conn()
    conn = next(connection_dependency)
    try:
        return life_service.ask(
            question,
            encoder=encoder,
            conn=conn,
            breed=breed,
            age_months=age_months,
            screening_verdict=screening_verdict,
            screening_days_ago=screening_days_ago,
            screening_history=screening_history,
        )
    finally:
        connection_dependency.close()


def _walk_life(payload: WalkPayload) -> Any:
    """Call the existing deterministic Walk service with domain-owned dependencies."""
    from daengs_life.app.deps import get_cache, get_now
    from daengs_life.app.services.walk import walk
    from daengs_life.realtime.geo import LatLon

    return walk(LatLon(payload.lat, payload.lon), get_now(), cache=get_cache())


def _weather_at_life(lat: float, lon: float, observed_at: datetime) -> WalkWeatherObservation:
    """Life의 공개 DTO 경계에서 과거 관측을 읽고 Walk용 값만 남긴다."""

    from daengs_life.app.deps import get_cache, get_now
    from daengs_life.app.dto.weather import WeatherAtRequest
    from daengs_life.app.services.weather import weather_at

    result = weather_at(
        WeatherAtRequest(lat=lat, lon=lon, observed_at=observed_at),
        get_now(),
        cache=get_cache(),
    )
    atoms = {item.quantity: item for item in result.observations}
    precip = atoms.get("precip_kind")
    amount = atoms.get("precip_mm")
    valid_times = {item.valid_at for item in result.observations}
    observed = next(iter(valid_times)) if len(valid_times) == 1 else None
    source = next(
        (item.provider for item in result.sources if item.outcome == "ok"),
        result.sources[0].provider if result.sources else None,
    )
    reason = next(
        (item.reason for item in result.sources if item.reason is not None),
        None,
    )
    return WalkWeatherObservation(
        status=result.status,
        provider=source,
        observed_at=observed,
        temperature_c=_number(atoms.get("temp_c"), minimum=-100, maximum=100),
        humidity_pct=_number(atoms.get("humidity_pct"), minimum=0, maximum=100),
        precipitation_kind=_precipitation_kind(str(precip.value) if precip is not None else None),
        precipitation_mm=_exact_interval(amount),
        failure_reason=_bounded_reason(reason) if result.status == "failed" else None,
    )


async def lookup_walk_weather(
    lat: float,
    lon: float,
    observed_at: datetime,
) -> WalkWeatherObservation:
    """동기 Life 전송을 event loop 밖에서 실행하고 예외를 실패 값으로 닫는다."""

    try:
        return await asyncio.to_thread(_weather_at_life, lat, lon, observed_at)
    except Exception as exc:  # noqa: BLE001 - 날씨 보강은 Walk 봉인을 실패시키지 않는다
        return WalkWeatherObservation(
            status="failed",
            failure_reason=f"Life 과거 날씨 조회 실패: {type(exc).__name__}",
        )


def _number(
    atom: Any | None,
    *,
    minimum: float,
    maximum: float,
) -> float | None:
    if atom is None or atom.representation != "number":
        return None
    value = float(atom.value)
    return value if minimum <= value <= maximum else None


def _exact_interval(atom: Any | None) -> float | None:
    if (
        atom is None
        or atom.representation != "interval"
        or atom.lower_bound is None
        or atom.upper_bound is None
        or atom.lower_bound != atom.upper_bound
    ):
        return None
    value = float(atom.lower_bound)
    return value if math.isfinite(value) and value >= 0 else None


def _precipitation_kind(value: str | None) -> WalkPrecipitationKind | None:
    """Life 의 `PrecipKind` → Walk 의 네 값. **여기는 일부러 거칠다.**

    Life 는 기상청 `PTY` 를 그대로 들고 있고(0~7, RT-004), Walk 는 비/눈/혼합이면 충분하다.
    그래서 세기(빗방울 vs 비)는 이 경계에서 접는다 — `shower`(소나기)를 `rain` 으로 접어 온
    것과 같은 규칙이다.

    ⚠️ **`Life` 에 값이 늘면 여기도 늘려야 한다.** 빠뜨리면 `.get()` 이 `None` 을 주고,
    Walk 는 그것을 "관측이 없다"로 읽는다 — KMA 가 값을 냈는데도 그렇다. 예외가 안 나서
    안 보이므로 `test_orchestration_walk_weather.py` 가 **누락 자체**를 잡는다.
    """
    return {
        "none": "none",
        "rain": "rain",
        "snow": "snow",
        "rain_snow": "mixed",
        "shower": "rain",
        # RT-004 — 초단기 계열의 셋. 세기만 다르고 형태는 위와 같아서 같은 자리로 접는다
        "drizzle": "rain",              # 5 빗방울
        "drizzle_snow": "mixed",        # 6 빗방울눈날림
        "snow_flurry": "snow",          # 7 눈날림
    }.get(value)


def _bounded_reason(value: str | None) -> str | None:
    return value[:256] if value else None


class LifeCapabilityAdapter:
    capability = CapabilityName.LIFE

    def __init__(self, ask: Callable[..., Any] | None = None) -> None:
        self._ask = ask or _ask_life

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        del request_id  # Life does not currently consume trace context at its domain boundary.
        started = time.perf_counter()
        payload = request.payload
        if not isinstance(payload, LifePayload):
            return self._error(started, "invalid_payload", "Life payload is invalid")
        dog = payload.dog
        screening = payload.screening
        history = payload.screening_history
        try:
            # 프로필이 없으면 두 값이 None 이고, 그때 Life 는 B4 이전과 똑같이 답한다.
            # 스크리닝도 같다 — 판정에서 이어 온 질문이 아니면 두 값이 None 이다 (#283).
            upstream = await asyncio.to_thread(
                partial(
                    self._ask,
                    payload.question,
                    breed=dog.breed if dog else None,
                    age_months=dog.age_months if dog else None,
                    screening_verdict=screening.verdict if screening else None,
                    screening_days_ago=screening.days_ago if screening else None,
                    screening_history=(
                        tuple((e.verdict, e.days_ago) for e in history.entries)
                        if history
                        else ()
                    ),
                )
            )
        except HTTPException as exc:
            code, detail = _outcome(exc.detail)
            if exc.status_code == 422:
                # Life refuses a question about this animal's body; keep its own wording intact.
                return CapabilityResult(
                    capability=self.capability,
                    status=CapabilityStatus.REFUSED,
                    refusal=OutcomeDetail(code=code or "life_boundary", message=detail),
                    elapsed_ms=_elapsed_ms(started),
                )
            if exc.status_code == 404:
                return CapabilityResult(
                    capability=self.capability,
                    status=CapabilityStatus.ABSTAINED,
                    abstention=OutcomeDetail(code=code or "no_evidence", message=detail),
                    elapsed_ms=_elapsed_ms(started),
                )
            if exc.status_code == 504:
                return CapabilityResult(
                    capability=self.capability,
                    status=CapabilityStatus.TIMEOUT,
                    error=ErrorDetail(kind="life_timeout", detail=detail),
                    elapsed_ms=_elapsed_ms(started),
                )
            return self._error(started, f"life_http_{exc.status_code}", detail)
        except TimeoutError:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.TIMEOUT,
                error=ErrorDetail(
                    kind="life_timeout", detail="생활 정보 응답 시간이 초과됐습니다."
                ),
                elapsed_ms=_elapsed_ms(started),
            )
        except Exception as exc:  # noqa: BLE001 - normalize an unexpected boundary failure
            return self._error(started, type(exc).__name__, "생활 정보 기능 실행에 실패했습니다.")

        citations = [
            {
                "label": hit.citation,
                "url": hit.citation_url,
                "document_title": hit.document_title,
            }
            for hit in upstream.hits
        ]
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={
                "answer": upstream.answer,
                "citations": citations,
                "quality": {"cited": upstream.cited, "ungrounded": upstream.ungrounded},
            },
            elapsed_ms=_elapsed_ms(started),
        )

    def _error(self, started: float, kind: str, detail: str) -> CapabilityResult:
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.ERROR,
            error=ErrorDetail(kind=kind, detail=detail),
            elapsed_ms=_elapsed_ms(started),
        )


def _outcome(detail: Any) -> tuple[str | None, str]:
    """Split Life's HTTPException detail into (code, message) without rewriting the message.

    Life sends a mapping for the outcomes it names itself and a bare string for the older
    ones. Reading both matters: ``str()`` over a mapping would hand the user a Python repr,
    which is exactly the lossy step invariant 3 forbids.
    """
    if isinstance(detail, dict):
        code = detail.get("code")
        message = detail.get("message")
        return (str(code) if code else None), (str(message) if message else str(detail))
    return None, str(detail)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1_000)


__all__ = [
    "LifeCapabilityAdapter",
    "WalkWeatherLookup",
    "WalkWeatherObservation",
    "lookup_walk_weather",
]
