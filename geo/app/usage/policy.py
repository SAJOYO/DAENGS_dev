"""정책 구현. 출고 기본은 거부, dev는 같은 Gate 안에서 작은 고정 한도만 허용한다."""

from dataclasses import dataclass, field
from typing import Protocol

from app.usage.models import (
    LanguageParseIntent,
    MeasuredRouteIntent,
    StaticMapIntent,
    UsageIntent,
    UsagePermit,
    UsageWindow,
)


class UsagePolicy(Protocol):
    async def decide(self, intent: UsageIntent) -> UsagePermit: ...


@dataclass(frozen=True)
class OperationLimit:
    request_units: int
    window_units: int
    window_seconds: int = 3600


@dataclass(frozen=True)
class DevUsageLimits:
    static_map: OperationLimit = field(default_factory=lambda: OperationLimit(1, 100))
    measured_route: OperationLimit = field(default_factory=lambda: OperationLimit(4, 60))
    language_parse: OperationLimit = field(default_factory=lambda: OperationLimit(1, 30))


class DenyAllPolicy:
    async def decide(self, intent: UsageIntent) -> UsagePermit:
        return UsagePermit(allowed=False, reason="paid usage policy is not configured")


class BoundedDevPolicy:
    """로컬 실API 검증용. 무제한 허용이 아니며 프로세스 단위 Ledger와 함께 쓴다."""

    def __init__(self, limits: DevUsageLimits | None = None):
        self._limits = limits or DevUsageLimits()

    async def decide(self, intent: UsageIntent) -> UsagePermit:
        limit = self._limit_for(intent)
        operation = intent.operation
        return UsagePermit(
            allowed=True,
            reason="bounded development usage",
            max_units_per_request=limit.request_units,
            window=UsageWindow(
                bucket=f"dev:{operation.value}",
                max_units=limit.window_units,
                seconds=limit.window_seconds,
            ),
        )

    def _limit_for(self, intent: UsageIntent) -> OperationLimit:
        if isinstance(intent, StaticMapIntent):
            return self._limits.static_map
        if isinstance(intent, MeasuredRouteIntent):
            return self._limits.measured_route
        if isinstance(intent, LanguageParseIntent):
            return self._limits.language_parse
        raise TypeError(f"unsupported usage intent: {type(intent).__name__}")
