"""누적 사용량 저장 계약과 개발/테스트용 프로세스 메모리 구현."""

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
    """원자적으로 예약하고 즉시 소비한다. 재시작·멀티워커 공유가 없는 dev 전용 구현."""

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

    async def snapshot(self) -> dict[str, int]:
        async with self._lock:
            return {bucket: counter.units for bucket, counter in self._counters.items()}
