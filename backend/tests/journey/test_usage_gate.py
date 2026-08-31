import pytest

from daengs_journey.providers.base import LatLng, Mode, RouteResult
from daengs_journey.usage.gate import UsageGate, usage_request_scope
from daengs_journey.usage.ledger import InMemoryLedger
from daengs_journey.usage.metered import MeteredRouteProvider
from daengs_journey.usage.models import UsageDenied
from daengs_journey.usage.policy import BoundedDevPolicy, DenyAllPolicy


class CountingProvider:
    name = "counting"
    route_modes = frozenset({"walk"})

    def __init__(self) -> None:
        self.calls = 0

    async def route(self, mode: Mode, origin: LatLng, dest: LatLng) -> RouteResult:
        self.calls += 1
        return RouteResult(mode, 100, 60, "counting")


@pytest.mark.asyncio
async def test_paid_route_is_denied_by_default() -> None:
    inner = CountingProvider()
    provider = MeteredRouteProvider(inner, UsageGate(DenyAllPolicy(), InMemoryLedger()))

    async with usage_request_scope():
        with pytest.raises(UsageDenied) as denied:
            await provider.route("walk", LatLng(37.5, 127.0), LatLng(37.6, 127.1))

    assert denied.value.code == "policy_denied"
    assert inner.calls == 0


@pytest.mark.asyncio
async def test_paid_route_keeps_original_four_calls_per_request_limit() -> None:
    inner = CountingProvider()
    provider = MeteredRouteProvider(inner, UsageGate(BoundedDevPolicy(), InMemoryLedger()))
    origin = LatLng(37.5, 127.0)

    async with usage_request_scope():
        for index in range(4):
            await provider.route("walk", origin, LatLng(37.6 + index / 100, 127.1))
        with pytest.raises(UsageDenied) as denied:
            await provider.route("walk", origin, LatLng(37.7, 127.1))

    assert denied.value.code == "request_limit"
    assert inner.calls == 4


@pytest.mark.asyncio
async def test_cache_hit_does_not_consume_another_unit() -> None:
    inner = CountingProvider()
    provider = MeteredRouteProvider(inner, UsageGate(BoundedDevPolicy(), InMemoryLedger()))
    origin = LatLng(37.5, 127.0)
    destination = LatLng(37.6, 127.1)

    async with usage_request_scope():
        first = await provider.route("walk", origin, destination)
        second = await provider.route("walk", origin, destination)

    assert second is first
    assert inner.calls == 1
