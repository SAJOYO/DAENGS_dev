from datetime import datetime
from zoneinfo import ZoneInfo

from app.geo.hours import is_open_at, today_ranges

KST = ZoneInfo("Asia/Seoul")
HOURS = {
    "tz": "Asia/Seoul",
    "weekly": {
        "0": [["09:00", "18:00"]],                     # 월
        "1": [["09:00", "12:00"], ["13:00", "18:00"]],  # 화 점심 휴게
        "4": [["22:00", "02:00"]],                     # 금 야간 (자정 넘김)
        "6": [],                                        # 일 휴무
    },
    "exceptions": {"2026-08-24": []},                 # 월요일 임시휴무
}


def kst(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=KST)


def test_open_weekday():
    assert is_open_at(HOURS, kst(2026, 8, 17, 10)) is True      # 월 10시
    assert is_open_at(HOURS, kst(2026, 8, 17, 18)) is False     # 종료 시각은 닫힘


def test_lunch_break():
    assert is_open_at(HOURS, kst(2026, 8, 18, 12, 30)) is False  # 화 점심
    assert is_open_at(HOURS, kst(2026, 8, 18, 13)) is True


def test_closed_day_and_missing_day():
    assert is_open_at(HOURS, kst(2026, 8, 23, 10)) is False  # 일 []
    assert is_open_at(HOURS, kst(2026, 8, 19, 10)) is False  # 수 = 키 없음 = 휴무


def test_overnight():
    assert is_open_at(HOURS, kst(2026, 8, 21, 23)) is True   # 금 23시
    assert is_open_at(HOURS, kst(2026, 8, 22, 1)) is True    # 토 새벽 1시 (전날 꼬리)
    assert is_open_at(HOURS, kst(2026, 8, 22, 3)) is False


def test_exception_overrides_weekly():
    assert is_open_at(HOURS, kst(2026, 8, 24, 10)) is False


def test_unknown_and_24h():
    assert is_open_at(None, kst(2026, 8, 17, 10)) is None
    assert is_open_at(None, kst(2026, 8, 17, 10), is_24h=True) is True


def test_utc_input_converted():
    # UTC 01:00 = KST 10:00 월요일
    assert is_open_at(HOURS, datetime(2026, 8, 17, 1, tzinfo=ZoneInfo("UTC"))) is True


def test_today_ranges():
    assert today_ranges(HOURS, kst(2026, 8, 18, 9)) == [("09:00", "12:00"), ("13:00", "18:00")]
    assert today_ranges(None, kst(2026, 8, 18, 9)) is None
