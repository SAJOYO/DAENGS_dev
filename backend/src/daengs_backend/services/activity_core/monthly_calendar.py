"""Modern Korean calendar months; no process-local timezone or timezone database dependency."""

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")


def month(at_ms):
    """Return stable ID and [start,end) UTC milliseconds for the containing KST month."""
    if type(at_ms) is not int or at_ms < 0:
        raise ValueError("invalid_calendar_time")
    now = datetime.fromtimestamp(at_ms / 1000, KST)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return f"territory-{start:%Y-%m}", int(start.timestamp() * 1000), int(end.timestamp() * 1000)
