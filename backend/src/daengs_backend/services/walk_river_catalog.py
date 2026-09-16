"""Historical background imports; implementation belongs to walk_background."""

from importlib import import_module

_EXPORTS = {
    "STANDARD": "daengs_backend.services.walk_background.catalogs.river",
    "EGIS": "daengs_backend.services.walk_background.catalogs.river",
    "TO_WEB": "daengs_backend.services.walk_background.catalogs.river",
    "FROM_WEB": "daengs_backend.services.walk_background.catalogs.river",
    "standard_row": "daengs_backend.services.walk_background.catalogs.river",
    "standard_metadata": "daengs_backend.services.walk_background.catalogs.river",
    "refresh": "daengs_backend.services.walk_background.catalogs.river",
    "nearby": "daengs_backend.services.walk_background.catalogs.river",
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
