"""영업시간 판정. 순수 함수 — DB·시간대 의존 없음, 테스트 쉬움.

hours JSON 형식:
{
  "tz": "Asia/Seoul",
  "weekly": {              # 0=월 … 6=일. 없는 요일 = 휴무
    "0": [["09:00","18:00"]],
    "5": [["09:00","13:00"], ["14:00","18:00"]],
    "6": []
  },
  "exceptions": {          # 특정일 오버라이드. [] = 휴무
    "2026-09-17": [],
    "2026-12-24": [["09:00","13:00"]]
  }
}
자정 넘김: ["22:00","02:00"] 처럼 end < start 면 다음날 새벽까지로 본다.
"""

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

Range = tuple[str, str]


def _parse(t: str) -> time:
    h, m = t.split(":")
    return time(int(h), int(m))


def _ranges_for(hours: dict[str, Any], d: date) -> list[Range]:
    exc = (hours.get("exceptions") or {}).get(d.isoformat())
    if exc is not None:
        return [tuple(r) for r in exc]
    weekly = hours.get("weekly") or {}
    return [tuple(r) for r in weekly.get(str(d.weekday()), [])]


def _in_range(now_t: time, start: str, end: str) -> bool:
    s, e = _parse(start), _parse(end)
    if s <= e:
        return s <= now_t < e
    return now_t >= s  # 자정 넘김의 앞부분 (뒷부분은 전날 판정에서 처리)


def _in_overnight_tail(now_t: time, start: str, end: str) -> bool:
    s, e = _parse(start), _parse(end)
    return s > e and now_t < e


def is_open_at(hours: dict[str, Any] | None, at: datetime, *, is_24h: bool = False) -> bool | None:
    """True/False, 정보 없으면 None (모름 ≠ 닫힘)."""
    if is_24h:
        return True
    if not hours:
        return None
    tz = ZoneInfo(hours.get("tz", "Asia/Seoul"))
    local = at.astimezone(tz)
    today, now_t = local.date(), local.time().replace(second=0, microsecond=0)

    for s, e in _ranges_for(hours, today):
        if _in_range(now_t, s, e):
            return True
    yesterday = today - timedelta(days=1)
    for s, e in _ranges_for(hours, yesterday):
        if _in_overnight_tail(now_t, s, e):
            return True
    return False


def today_ranges(hours: dict[str, Any] | None, at: datetime) -> list[Range] | None:
    if not hours:
        return None
    tz = ZoneInfo(hours.get("tz", "Asia/Seoul"))
    return _ranges_for(hours, at.astimezone(tz).date())
