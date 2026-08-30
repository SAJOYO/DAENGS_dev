import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

from app.usage.models import UsageWindow


class UsageLedger(Protocol):
    async def reserve(self, window: UsageWindow, units: int) -> bool: ...


@dataclass
class _Counter:
    started_at: float
    units: int = 0


class InMemoryLedger:
    """The process-local ledger used by DAENGS_geo's bounded development policy."""

    def __init__(self):
        self._counters: dict[str, _Counter] = {}
        self._lock = asyncio.Lock()

    async def reserve(self, window: UsageWindow, units: int) -> bool:
        now = time.monotonic()
        async with self._lock:
            counter = self._counters.get(window.bucket)
            if counter is None or now - counter.started_at >= window.seconds:
                counter = _Counter(started_at=now)
                self._counters[window.bucket] = counter
            if counter.units + units > window.max_units:
                return False
            counter.units += units
            return True
