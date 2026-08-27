"""기상청 단기예보 — 실황 · 초단기예보 · 단기예보 (RT-001 ①).

**세 오퍼레이션이 한 모듈인 이유** — 같은 봉투·같은 격자 입력이고 API *서비스* 하나다. 그래서
provider 7개가 `docs/data-sources.md` §8 의 연동 체크 7개와 1:1 로 맞는다. 다만 `Source` 는
셋으로 갈린다 — ⑤-d 의 우선순위가 오퍼레이션 단위로 매겨지기 때문이다.

**이 모듈이 흡수하는 개명이 두 겹이다** (§6.3):
  값 필드   `obsrValue`(실황) → `fcstValue`(예보)
  양의 이름 `T1H`(실황·초단기) → **`TMP`**(단기예보)
룰은 `Q.TEMP` 하나만 안다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..config import KST
from ..geo import Grid
from ..observation import (
    Interval, Measurement, Q, Source, parse_interval, parse_precip_kind, parse_sky,
)
from ..transport import datagokr
from ..transport.base import Budget

PATH = "/1360000/VilageFcstInfoService_2.0"

# category → 관측량. **`T1H` 와 `TMP` 가 같은 자리로 간다** — 개명이 여기서 끝난다.
CATEGORY: dict[str, Q] = {
    "T1H": Q.TEMP, "TMP": Q.TEMP, "TMN": Q.TEMP_MIN, "TMX": Q.TEMP_MAX,
    "REH": Q.HUMIDITY, "WSD": Q.WIND, "UUU": Q.WIND_U, "VVV": Q.WIND_V, "VEC": Q.WIND_DIR,
    "PTY": Q.PRECIP_KIND, "SKY": Q.SKY,
    "RN1": Q.PRECIP_MM, "PCP": Q.PRECIP_MM, "SNO": Q.SNOW_CM,
    "POP": Q.PRECIP_PROB, "LGT": Q.LIGHTNING, "WAV": Q.WAVE_M,
}


def _when(date: str, time: str) -> datetime:
    return datetime.strptime(f"{date}{time}", "%Y%m%d%H%M").replace(tzinfo=KST)


def _value(q: Q, raw: str) -> Any:
    """문자열 하나를 그 양의 표현으로. **도메인 고정이 provider 의 일이라는 것**이 여기다.

    `RN1` 이 실황에서는 `'0'`(숫자), 예보에서는 `'강수없음'`(구간)으로 온다 — 같은 이름의
    도메인이 오퍼레이션마다 흔들린다. `parse_interval` 이 둘 다 받아 폭 0 구간으로 통일한다.
    """
    if q is Q.PRECIP_KIND:
        return parse_precip_kind(raw)
    if q is Q.SKY:
        return parse_sky(raw)
    if q in (Q.PRECIP_MM, Q.SNOW_CM):
        return parse_interval(raw)
    return float(raw)


def _parse(body: dict, source: Source, *, value_key: str, forecast: bool) -> list[Measurement]:
    items = (body.get("items") or {}).get("item") or []
    out: list[Measurement] = []
    for item in items:
        q = CATEGORY.get(item.get("category", ""))
        if q is None:
            # 모르는 category 는 조용히 버린다 — 실패가 아니다. `Q` 는 닫힌 어휘이고
            # 여기 없는 코드는 "아직 안 쓰는 양"이지 오류가 아니다 (②-d-3 "넓게 잡은 이유").
            continue
        issued = _when(item["baseDate"], item["baseTime"])
        valid = _when(item["fcstDate"], item["fcstTime"]) if forecast else issued
        out.append(Measurement(
            quantity=q, value=_value(q, str(item[value_key])),
            # 실황은 `valid_at == issued_at` 이다. 관측을 "리드타임 0인 측정값"으로 본 ②-c 가
            # 그대로 성립하는 자리 — 제공이 +40분 늦는 것은 조회 시점의 문제이지 값의 시각이 아니다.
            valid_at=valid, issued_at=issued,
            source=source, spatial_ref=f"격자 {item['nx']},{item['ny']}"))
    return out


def parse_ncst(body: dict) -> list[Measurement]:
    """초단기실황 — 모양 A. `obsrValue`, 시각 하나."""
    return _parse(body, Source.NCST, value_key="obsrValue", forecast=False)


def parse_ultra(body: dict) -> list[Measurement]:
    """초단기예보 — 모양 B. `T1H` 를 유지한다."""
    return _parse(body, Source.FCST_ULTRA, value_key="fcstValue", forecast=True)


def parse_village(body: dict) -> list[Measurement]:
    """단기예보 — 모양 B. 기온이 **`TMP`** 로 개명된다."""
    return _parse(body, Source.FCST_VILLAGE, value_key="fcstValue", forecast=True)


# --- 발표 시각 (④-b 실측 발표 주기에서 유도. 임의 숫자 없음) --------------------

def ncst_base(now: datetime) -> tuple[str, str]:
    """매시 정각 관측, **+40분 제공**. 40분 전 시각의 정시를 부른다."""
    t = now - timedelta(minutes=40)
    return t.strftime("%Y%m%d"), t.strftime("%H00")


def ultra_base(now: datetime) -> tuple[str, str]:
    """매시 `:30` 발표, `:45` 제공."""
    t = now - timedelta(minutes=45)
    return t.strftime("%Y%m%d"), t.strftime("%H30")


VILLAGE_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)


def village_base(now: datetime) -> tuple[str, str]:
    """02·05·…·23시 발표, `:10` 제공. 그중 지금 이전의 가장 늦은 발표."""
    t = now - timedelta(minutes=10)
    for hour in reversed(VILLAGE_HOURS):
        if t.hour >= hour:
            return t.strftime("%Y%m%d"), f"{hour:02d}00"
    return (t - timedelta(days=1)).strftime("%Y%m%d"), "2300"


# --- 조회 --------------------------------------------------------------------

def _fetch(operation: str, grid: Grid, base: tuple[str, str], rows: int,
           budget: Budget | None) -> dict:
    date, time = base
    return datagokr.get(f"{PATH}/{operation}",
                        {"base_date": date, "base_time": time,
                         "nx": grid.nx, "ny": grid.ny, "numOfRows": rows},
                        budget=budget)


# --- 받기와 파싱을 가른다 (RT-002 ②-a) ---------------------------------------
# 캐시가 저장하는 것은 **원본 응답**이라(`cache.py`) 조립층은 `raw_*` 로 받아 두었다가
# 히트일 때 같은 `parse_*` 를 다시 먹인다. URL·파라미터 지식은 provider 에 남는다.

def raw_ncst(grid: Grid, now: datetime, *, budget: Budget | None = None) -> dict:
    return _fetch("getUltraSrtNcst", grid, ncst_base(now), 10, budget)


def raw_ultra(grid: Grid, now: datetime, *, budget: Budget | None = None) -> dict:
    return _fetch("getUltraSrtFcst", grid, ultra_base(now), 100, budget)


def raw_village(grid: Grid, now: datetime, *, budget: Budget | None = None) -> dict:
    # **1000 행을 요구한다** — 아래 `fetch` 의 주석 참고
    return _fetch("getVilageFcst", grid, village_base(now), 1000, budget)


def fetch(grid: Grid, now: datetime, *, budget: Budget | None = None
          ) -> tuple[list[Measurement], list[Measurement], list[Measurement]]:
    """셋을 각각 부른다. 호출부가 부분 실패를 잡을 수 있게 예외는 그대로 올린다."""
    return (
        parse_ncst(_fetch("getUltraSrtNcst", grid, ncst_base(now), 10, budget)),
        parse_ultra(_fetch("getUltraSrtFcst", grid, ultra_base(now), 100, budget)),
        # **1000 행을 요구한다.** 기본값 100 이면 871건 중 100건만 받아 T+24h 타임라인이
        # 9시간에서 끊긴다 (실측 2026-08-25). 기상청 백엔드는 1000 행에서 4/4 정상이었다.
        parse_village(_fetch("getVilageFcst", grid, village_base(now), 1000, budget)),
    )
