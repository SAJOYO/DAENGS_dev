"""Historical background imports; implementation belongs to walk_background."""

from importlib import import_module

_EXPORTS = {
    "FORWARD": "daengs_backend.services.walk_background.catalogs.area",
    "REVERSE": "daengs_backend.services.walk_background.catalogs.area",
    "label": "daengs_backend.services.walk_background.catalogs.area",
    "area": "daengs_backend.services.walk_background.catalogs.area",
    "xy": "daengs_backend.services.walk_background.catalogs.area",
    "covers": "daengs_backend.services.walk_background.catalogs.area",
    "pages": "daengs_backend.services.walk_background.catalogs.area",
    "unique_rows": "daengs_backend.services.walk_background.catalogs.area",
    "publish": "daengs_backend.services.walk_background.catalogs.area",
    "read": "daengs_backend.services.walk_background.catalogs.area",
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
