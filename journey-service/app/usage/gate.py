import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from app.usage.ledger import UsageLedger
from app.usage.models import MeasuredRouteIntent, UsageDenied, UsagePermit
from app.usage.policy import UsagePolicy


@dataclass
class _RequestUsage:
    units: dict[str, int] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_request_usage: ContextVar[_RequestUsage | None] = ContextVar("request_usage", default=None)


@asynccontextmanager
async def usage_request_scope() -> AsyncIterator[None]:
    token = _request_usage.set(_RequestUsage())
    try:
        yield
    finally:
        _request_usage.reset(token)


class UsageGate:
    def __init__(self, policy: UsagePolicy, ledger: UsageLedger):
        self._policy = policy
        self._ledger = ledger

    async def check(self, intent: MeasuredRouteIntent) -> UsagePermit:
        permit = await self._policy.decide(intent)
        if not permit.allowed:
            raise UsageDenied("policy_denied", permit.reason)
        return permit

    async def consume(self, intent: MeasuredRouteIntent, permit: UsagePermit) -> None:
        limit = permit.max_units_per_request
        if limit is not None:
            scope = _request_usage.get()
            if scope is None:
                raise UsageDenied(
                    "request_scope_missing",
                    "metered usage requires an explicit request scope",
                )
            async with scope.lock:
                used = scope.units.get(intent.operation, 0)
                if used + intent.units > limit:
                    raise UsageDenied(
                        "request_limit",
                        f"{intent.operation} request limit exceeded",
                    )
                scope.units[intent.operation] = used + intent.units

        if permit.window is not None and not await self._ledger.reserve(
            permit.window, intent.units
        ):
            raise UsageDenied(
                "usage_limit",
                f"{intent.operation} usage limit exceeded",
                retry_after_s=permit.window.seconds,
            )
