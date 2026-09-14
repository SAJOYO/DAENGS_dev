"""Historical background imports; implementation belongs to walk_background."""

from importlib import import_module

_EXPORTS = {
    "ENDPOINT": "daengs_backend.services.walk_background.catalogs.park",
    "FORMAT": "daengs_backend.services.walk_background.catalogs.park",
    "park_row": "daengs_backend.services.walk_background.catalogs.park",
    "parse_page": "daengs_backend.services.walk_background.catalogs.park",
    "refresh_catalog": "daengs_backend.services.walk_background.catalogs.park",
    "read_catalog": "daengs_backend.services.walk_background.catalogs.park",
    "distance_m": "daengs_backend.services.walk_background.catalogs.park",
    "nearby_parks": "daengs_backend.services.walk_background.catalogs.park",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_EXPORTS[name]), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
