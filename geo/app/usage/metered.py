"""실제 provider를 Usage Gate 뒤에 가두는 어댑터."""

import time
from collections import OrderedDict
from typing import Protocol

from app.providers.base import (
    LatLng,
    MapProvider,
    Mode,
    RouteResult,
    StaticMapSpec,
)
from app.usage.gate import UsageGate
from app.usage.models import MeasuredRouteIntent, StaticMapIntent

_ROUTE_TTL = {"walk": 6 * 3600, "car": 600, "transit": 1800}
_ROUTE_CACHE_MAX = 5000


class StaticMapFetcher(Protocol):
    async def fetch_static_png(self, spec: StaticMapSpec) -> bytes: ...


class MeteredStaticMapFetcher:
    def __init__(self, inner: StaticMapFetcher, gate: UsageGate):
        self._inner = inner
        self._gate = gate

    async def fetch_static_png(self, spec: StaticMapSpec) -> bytes:
        intent = StaticMapIntent(
            width=spec.width,
            height=spec.height,
            marker_count=len(spec.markers),
        )
        permit = await self._gate.check(intent)
        await self._gate.consume(intent, permit)
        return await self._inner.fetch_static_png(spec)


class MeteredRouteProvider:
    """인가는 캐시보다 먼저 확인하고, 요청당·누적 사용량은 cache miss 에서만 소비한다."""

    def __init__(self, inner: MapProvider, gate: UsageGate):
        self._inner = inner
        self._gate = gate
        self.name = inner.name
        self.route_modes = inner.route_modes
        self._cache: OrderedDict[tuple, tuple[float, RouteResult]] = OrderedDict()

    def static_map_url(self, spec: StaticMapSpec) -> str | None:
        return self._inner.static_map_url(spec)

    async def geocode(self, address: str) -> LatLng | None:
        return await self._inner.geocode(address)

    async def reverse_geocode(self, pos: LatLng) -> str | None:
        return await self._inner.reverse_geocode(pos)

    async def route(self, mode: Mode, origin: LatLng, dest: LatLng) -> RouteResult | None:
        intent = MeasuredRouteIntent(mode=mode)
        permit = await self._gate.check(intent)

        key = self._cache_key(mode, origin, dest)
        hit = self._cache.get(key)
        if hit:
            if time.monotonic() - hit[0] < _ROUTE_TTL[mode]:
                # 조회도 사용이다. 최근에 사용한 항목을 끝으로 보내야 축출이 진짜 LRU가 된다.
                self._cache.move_to_end(key)
                return hit[1]
            # 만료 항목을 같은 자리에 덮어쓰면 OrderedDict의 순서가 유지된다.
            # 먼저 제거해 새 응답이 가장 최근 항목으로 들어가게 한다.
            del self._cache[key]

        await self._gate.consume(intent, permit)
        result = await self._inner.route(mode, origin, dest)
        if result is not None:
            if len(self._cache) >= _ROUTE_CACHE_MAX:
                self._cache.popitem(last=False)
            self._cache[key] = (time.monotonic(), result)
        return result

    def cache_size(self) -> int:
        return len(self._cache)

    @staticmethod
    def _cache_key(mode: Mode, origin: LatLng, dest: LatLng) -> tuple:
        # 도보 옵션 차원은 #66 으로 없앴다 — 가능한 값이 하나뿐이라 분할 역할을 못 했다.
        # 캐시는 프로세스 메모리라 배포 시 그냥 비워진다.
        return (
            mode,
            round(origin.lat, 4),
            round(origin.lng, 4),
            round(dest.lat, 4),
            round(dest.lng, 4),
        )
