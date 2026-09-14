"""Historical background imports; implementation belongs to walk_background."""

from importlib import import_module

_EXPORTS = {
    "RADIUS_M": "daengs_backend.services.walk_background.catalogs.regions",
    "REFRESH_DAYS": "daengs_backend.services.walk_background.catalogs.regions",
    "region": "daengs_backend.services.walk_background.catalogs.regions",
    "path_for": "daengs_backend.services.walk_background.catalogs.regions",
    "automatic_ready": "daengs_backend.services.walk_background.catalogs.regions",
    "can_prepare": "daengs_backend.services.walk_background.catalogs.regions",
    "select": "daengs_backend.services.walk_background.catalogs.regions",
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
