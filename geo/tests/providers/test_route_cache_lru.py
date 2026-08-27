"""경로 캐시는 삽입 순서가 아니라 최근 사용 순서로 축출한다."""

from app.providers.base import LatLng, RouteResult
from app.usage import metered as metered_module
from app.usage.gate import UsageGate, usage_request_scope
from app.usage.ledger import InMemoryLedger
from app.usage.metered import MeteredRouteProvider
from app.usage.policy import BoundedDevPolicy, DevUsageLimits, OperationLimit


class SpyRouteProvider:
    name = "spy-route"
    route_modes = frozenset({"walk"})

    def __init__(self):
        self.calls = 0

    def static_map_url(self, spec):
        return None

    async def geocode(self, address):
        return None

    async def reverse_geocode(self, pos):
        return None

    async def route(self, mode, origin, dest):
        self.calls += 1
        return RouteResult(
            mode=mode,
            distance_m=100,
            duration_s=60,
            source=self.name,
        )


def route_gate() -> UsageGate:
    limits = DevUsageLimits(measured_route=OperationLimit(request_units=10, window_units=10))
    return UsageGate(BoundedDevPolicy(limits), InMemoryLedger())


async def test_recently_used_route_survives_lru_eviction(monkeypatch):
    """A를 다시 읽은 뒤 C를 넣으면 가장 오래 안 쓴 B가 빠져야 한다."""
    monkeypatch.setattr(metered_module, "_ROUTE_CACHE_MAX", 2)
    spy = SpyRouteProvider()
    provider = MeteredRouteProvider(spy, route_gate())
    origin = LatLng(37.5, 127.0)
    a = LatLng(37.51, 127.01)
    b = LatLng(37.52, 127.02)
    c = LatLng(37.53, 127.03)

    async with usage_request_scope():
        await provider.route("walk", origin, a)  # miss: [A]
        await provider.route("walk", origin, b)  # miss: [A, B]
        await provider.route("walk", origin, a)  # hit:  [B, A]
        await provider.route("walk", origin, c)  # miss: evict B -> [A, C]
        await provider.route("walk", origin, a)  # hit: A must still be cached

    assert spy.calls == 3
    assert provider.cache_size() == 2

    async with usage_request_scope():
        await provider.route("walk", origin, b)  # B was the LRU victim, so this is a miss

    assert spy.calls == 4
