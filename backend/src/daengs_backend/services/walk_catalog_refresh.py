"""Historical background imports; implementation belongs to walk_background."""

from importlib import import_module

_EXPORTS = {
    "LOCK": "daengs_backend.services.walk_background.catalogs.refresh",
    "RELEASE": "daengs_backend.services.walk_background.catalogs.refresh",
    "BUDGET": "daengs_backend.services.walk_background.catalogs.refresh",
    "ERRORS": "daengs_backend.services.walk_background.catalogs.refresh",
    "BudgetTransport": "daengs_backend.services.walk_background.catalogs.refresh",
    "fresh": "daengs_backend.services.walk_background.catalogs.refresh",
    "ready": "daengs_backend.services.walk_background.catalogs.refresh",
    "cycle": "daengs_backend.services.walk_background.catalogs.refresh",
    "run": "daengs_backend.services.walk_background.catalogs.refresh",
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
