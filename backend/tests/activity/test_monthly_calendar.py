"""Calendar math is independent of server timezone, leap years, and partial first seasons."""

from datetime import datetime

import pytest

from daengs_backend.services.activity_core.monthly_calendar import month


def stamp(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


@pytest.mark.parametrize(
    "now,key,start,end",
    [
        (
            "2026-09-11T13:20:00+09:00",
            "territory-2026-09",
            "2026-09-01T00:00:00+09:00",
            "2026-10-01T00:00:00+09:00",
        ),
        (
            "2026-09-30T14:59:59.999+00:00",
            "territory-2026-09",
            "2026-09-01T00:00:00+09:00",
            "2026-10-01T00:00:00+09:00",
        ),
        (
            "2026-09-30T15:00:00+00:00",
            "territory-2026-10",
            "2026-10-01T00:00:00+09:00",
            "2026-11-01T00:00:00+09:00",
        ),
        (
            "2026-12-31T23:59:59+09:00",
            "territory-2026-12",
            "2026-12-01T00:00:00+09:00",
            "2027-01-01T00:00:00+09:00",
        ),
        (
            "2028-02-29T12:00:00+09:00",
            "territory-2028-02",
            "2028-02-01T00:00:00+09:00",
            "2028-03-01T00:00:00+09:00",
        ),
        (
            "2027-02-28T23:59:59+09:00",
            "territory-2027-02",
            "2027-02-01T00:00:00+09:00",
            "2027-03-01T00:00:00+09:00",
        ),
    ],
)
def test_calendar(now, key, start, end):
    assert month(stamp(now)) == (key, stamp(start), stamp(end))


@pytest.mark.parametrize("value", [-1, 1.2, True, "2026-09"])
def test_invalid_clock(value):
    with pytest.raises(ValueError, match="invalid_calendar_time"):
        month(value)
