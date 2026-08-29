from functools import lru_cache

from app.providers.base import Mode, RouteProvider
from app.providers.registry import build_raw_provider, route_provider_name
from app.usage.metered import MeteredRouteProvider
from app.usage.registry import usage_gate


@lru_cache
def route_provider(mode: Mode) -> RouteProvider:
    provider = build_raw_provider(route_provider_name(mode))
    if provider.name in ("none", "fake"):
        return provider
    return MeteredRouteProvider(provider, usage_gate())


def route_cache_stats() -> dict[str, int]:
    size = 0
    for mode in ("walk", "car", "transit"):
        provider = route_provider(mode)
        if isinstance(provider, MeteredRouteProvider):
            size += provider.cache_size()
    return {"size": size}


def route_capability_problems() -> list[str]:
    problems: list[str] = []
    for mode in ("walk", "car", "transit"):
        name = route_provider_name(mode)
        if name in ("none", "fake"):
            continue
        provider = route_provider(mode)
        if provider.name == "none":
            problems.append(f"{mode}: '{name}' provider key is missing")
        elif mode not in provider.route_modes:
            supported = ", ".join(sorted(provider.route_modes)) or "none"
            problems.append(f"{mode}: '{name}' does not implement this mode ({supported})")
    return problems
