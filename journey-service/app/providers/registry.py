from app.core.config import settings
from app.providers.base import Mode, NullProvider, RouteProvider
from app.providers.fake import FakeProvider
from app.providers.tmap import TmapProvider


def route_provider_name(mode: Mode) -> str:
    return {
        "walk": settings.walk_route_provider,
        "car": settings.car_route_provider,
        "transit": settings.transit_route_provider,
    }[mode]


def build_raw_provider(name: str) -> RouteProvider:
    if name == "fake":
        return FakeProvider()
    if name == "tmap" and settings.tmap_app_key:
        return TmapProvider(settings.tmap_app_key)
    return NullProvider()
