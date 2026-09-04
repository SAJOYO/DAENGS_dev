import time
from collections import OrderedDict

from daengs_journey.providers.base import LatLng, Mode, RouteProvider, RouteResult
from daengs_journey.usage.gate import UsageGate
from daengs_journey.usage.models import MeasuredRouteIntent

_ROUTE_TTL = {"walk": 6 * 3600, "car": 600, "transit": 1800}
_ROUTE_CACHE_MAX = 5000


class MeteredRouteProvider:
    def __init__(self, inner: RouteProvider, gate: UsageGate):
        self._inner = inner
        self._gate = gate
        self.name = inner.name
        self.route_modes = inner.route_modes
        self._cache: OrderedDict[tuple, tuple[float, RouteResult]] = OrderedDict()

    async def route(self, mode: Mode, origin: LatLng, dest: LatLng) -> RouteResult | None:
        intent = MeasuredRouteIntent(mode=mode)
        permit = await self._gate.check(intent)
        key = self._cache_key(mode, origin, dest)
        hit = self._cache.get(key)
        if hit:
            if time.monotonic() - hit[0] < _ROUTE_TTL[mode]:
                self._cache.move_to_end(key)
                return hit[1]
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
        return (
            mode,
            round(origin.lat, 4),
            round(origin.lng, 4),
            round(dest.lat, 4),
            round(dest.lng, 4),
        )
