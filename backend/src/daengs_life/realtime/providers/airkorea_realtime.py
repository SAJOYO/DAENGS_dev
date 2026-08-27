"""에어코리아 대기오염정보 — 측정소 실시간(모양 D) · 미세먼지 예보통보(모양 E).

**등급이 두 벌인 것을 여기서 흡수한다** (②-d-2 근거 4). 실측 강남대로에서 PM10 이
`pm10Grade`(24h 평균 기준)와 `pm10Grade1h`(현재 기준)로 갈렸다. 산책은 지금 나가는 것이므로
**`Grade1h`** 를 싣는다. 룰이 등급을 읽는다면 매번 "어느 등급이지"를 고민하게 되므로,
그 선택을 이 층에서 한 번에 끝낸다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..config import KST
from ..observation import Measurement, Q, Source, State, StateKind
from ..transport import datagokr
from ..transport.base import Budget

PATH = "/B552584/ArpltnInforInqireSvc"

_ONE_DAY = timedelta(days=1)
_ZERO = timedelta(0)

# 값 필드 → (관측량, 등급 필드). **등급 필드가 두 종류다** — PM 둘만 `Grade1h` 를 주고
# 나머지는 `Grade` 뿐이다. 있는 쪽을 고르되 **PM 은 반드시 `Grade1h`** 여야 한다.
FIELDS: dict[str, tuple[Q, str]] = {
    "pm10Value": (Q.PM10, "pm10Grade1h"),
    "pm25Value": (Q.PM25, "pm25Grade1h"),
    "so2Value": (Q.SO2, "so2Grade"),
    "no2Value": (Q.NO2, "no2Grade"),
    "coValue": (Q.CO, "coGrade"),
    "o3Value": (Q.O3, "o3Grade"),
    # 여러 오염물질을 합성한 지수라 우리가 재현할 수 없다 — 값 자체를 하나의 양으로 싣는다 (②-d-2)
    "khaiValue": (Q.KHAI, "khaiGrade"),
}

# 권역 등급 예보의 라벨. 기관이 준 그대로 쓴다 (②-d-2 — 정규화하면 룰이 그걸로 판정하고 싶어진다)
FORECAST_CODES = {"PM10": Q.PM10, "PM25": Q.PM25, "O3": Q.O3}


def _number(raw: Any) -> float | None:
    """`'-'` 나 빈 값은 결측이다. 0 으로 떨어뜨리면 "공기가 깨끗하다"가 된다."""
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _grade(raw: Any) -> int | None:
    value = _number(raw)
    return int(value) if value is not None else None


def parse_dnsty(body: dict, station: str) -> list[Measurement]:
    """측정소 실시간 — 모양 D.

    `dataTerm=DAILY` 는 측정소 여러 곳이 아니라 **한 측정소의 24시간 시계열**을 준다 (§6.3).
    그래서 `valid_at` 이 행마다 다르고 목록이 그대로 시계열이 된다.
    """
    out: list[Measurement] = []
    for item in body.get("items") or []:
        when = _dtime(item.get("dataTime"))
        if when is None:
            continue
        for field, (q, grade_field) in FIELDS.items():
            value = _number(item.get(field))
            if value is None:
                continue                      # 결측·점검(`*Flag`)은 값이 없는 것이지 0 이 아니다
            out.append(Measurement(
                quantity=q, value=value,
                # 관측이라 시각이 하나다 (②-c — 리드타임 0)
                valid_at=when, issued_at=when,
                source=Source.AIRKOREA, spatial_ref=f"측정소 {station}",
                grade=_grade(item.get(grade_field))))
    return out


def _dtime(raw: Any) -> datetime | None:
    """`'2026-08-25 09:00'`. 실측에서 `'24:00'` 이 오는 API 계열이 있어 그것도 받는다."""
    text = str(raw or "").strip()
    if not text:
        return None
    date, _, clock = text.partition(" ")
    hour, _, minute = clock.partition(":")
    try:
        base = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=KST)
        return base.replace(hour=int(hour) % 24, minute=int(minute or 0)) + (
            _ONE_DAY if int(hour) >= 24 else _ZERO)
    except (TypeError, ValueError):
        return None


def parse_frcst(body: dict, region: str) -> list[State]:
    """미세먼지 예보통보 — 모양 E. **값이 아니라 상태다.**

    `informGrade` 가 `"서울 : 좋음,제주 : 좋음,…"` 한 문자열이라 권역을 문자열에서 뽑아야 한다.
    수치가 없고 (하루 구간 · 권역 · 범주) 뿐이라 ②-a 의 상태 정의에 그대로 맞는다.

    ③-b 가 **미래 대기질을 이 권역 일 등급으로 대신한다**고 정한 자리다 — 측정소 실측값의
    persistence 는 기각됐다.
    """
    out: list[State] = []
    for item in body.get("items") or []:
        q = FORECAST_CODES.get(str(item.get("informCode", "")).strip())
        day = _date(item.get("informData"))
        issued = _issued(item.get("dataTime"))
        if q is None or day is None or issued is None:
            continue
        category = _grade_for(str(item.get("informGrade") or ""), region)
        if category is None:
            continue                          # 그 권역이 통보에 없다 — 없는 것을 지어내지 않는다
        out.append(State(
            kind=StateKind.AIR_FORECAST, category=category, area=region,
            valid_from=day, valid_to=day + _ONE_DAY, issued_at=issued,
            source=Source.AIRKOREA_FORECAST, detail=f"{q.name} {item.get('informOverall') or ''}".strip()))
    return out


def _grade_for(inform_grade: str, region: str) -> str | None:
    """`"서울 : 좋음,경남 : 보통,…"` 에서 내 권역의 라벨. 없으면 `None`."""
    for chunk in inform_grade.split(","):
        name, sep, label = chunk.partition(":")
        if sep and name.strip() == region:
            return label.strip()
    return None


def _date(raw: Any) -> datetime | None:
    try:
        return datetime.strptime(str(raw).strip(), "%Y-%m-%d").replace(tzinfo=KST)
    except (TypeError, ValueError):
        return None


def _issued(raw: Any) -> datetime | None:
    """`'2026-08-25 05시 발표'` — 날짜와 시각 사이에 한국어가 섞인다."""
    text = str(raw or "").strip()
    date, _, rest = text.partition(" ")
    hour = "".join(c for c in rest if c.isdigit())
    try:
        return datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=KST, hour=int(hour[:2]))
    except (TypeError, ValueError):
        return None


# --- 조회 --------------------------------------------------------------------

# --- 받기와 파싱을 가른다 (RT-002 ②-a) ---------------------------------------
# 캐시가 저장하는 것은 **원본 응답**이라(`cache.py`) 조립층은 `raw_*` 로 받아 두었다가
# 히트일 때 같은 `parse_*` 를 다시 먹인다. URL·파라미터 지식은 provider 에 남는다.

def raw_dnsty(station: str, *, budget: Budget | None = None) -> dict:
    return datagokr.get(f"{PATH}/getMsrstnAcctoRltmMesureDnsty",
                        {"stationName": station, "dataTerm": "DAILY", "ver": "1.3",
                         "numOfRows": 24}, budget=budget)


def raw_frcst(day: datetime, *, budget: Budget | None = None) -> dict:
    return datagokr.get(f"{PATH}/getMinuDustFrcstDspth",
                        {"searchDate": day.strftime("%Y-%m-%d"), "InformCode": "PM10",
                         "numOfRows": 10}, budget=budget)


def fetch_dnsty(station: str, *, budget: Budget | None = None) -> list[Measurement]:
    # `numOfRows` 를 키우지 않는다 — 이 백엔드는 1000 행에서 4번 중 2번 504 다 (실측 2026-08-25).
    # 24시간 시계열이라 기본값 100 으로 충분하다.
    return parse_dnsty(datagokr.get(f"{PATH}/getMsrstnAcctoRltmMesureDnsty",
                                    {"stationName": station, "dataTerm": "DAILY", "ver": "1.3",
                                     "numOfRows": 24}, budget=budget), station)


def fetch_frcst(region: str, day: datetime, *, budget: Budget | None = None) -> list[State]:
    return parse_frcst(datagokr.get(f"{PATH}/getMinuDustFrcstDspth",
                                    {"searchDate": day.strftime("%Y-%m-%d"), "InformCode": "PM10",
                                     "numOfRows": 10}, budget=budget), region)
