"""Local display time for Korean diary requests; stored source instants stay intact."""

from datetime import datetime
from zoneinfo import ZoneInfo

DIARY_TIMEZONE = "Asia/Seoul"
_ZONE = ZoneInfo(DIARY_TIMEZONE)
_INSTANT_FIELDS = frozenset({
    "started_at", "ended_at", "recorded_at", "event_at", "location_at",
    "measured_at", "support_started_at", "support_ended_at",
    "representative_event_at", "at", "start", "end",
})


def local_writer_times(value):
    """Project only declared instant fields in an already selected writer view.

    IDs, prose, date-only catalog references and elapsed seconds are not rewritten.
    Missing offsets are rejected instead of assuming the host machine's timezone.
    """
    if isinstance(value, list):
        return [local_writer_times(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in _INSTANT_FIELDS and isinstance(item, str):
            instant = datetime.fromisoformat(item)
            if instant.utcoffset() is None:
                raise ValueError("writer instant requires a timezone offset")
            result[key] = instant.astimezone(_ZONE).isoformat()
        else:
            result[key] = local_writer_times(item)
    return result
