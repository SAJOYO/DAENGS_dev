from typing import Protocol

from daengs_journey.usage.models import MeasuredRouteIntent, UsagePermit, UsageWindow


class UsagePolicy(Protocol):
    async def decide(self, intent: MeasuredRouteIntent) -> UsagePermit: ...


class DenyAllPolicy:
    async def decide(self, intent: MeasuredRouteIntent) -> UsagePermit:
        return UsagePermit(allowed=False, reason="paid usage policy is not configured")


class BoundedDevPolicy:
    """Original limits: four paid routes per request and sixty per process-hour."""

    async def decide(self, intent: MeasuredRouteIntent) -> UsagePermit:
        return UsagePermit(
            allowed=True,
            reason="bounded development usage",
            max_units_per_request=4,
            window=UsageWindow(
                bucket=f"dev:{intent.operation}",
                max_units=60,
                seconds=3600,
            ),
        )
