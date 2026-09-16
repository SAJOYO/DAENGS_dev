"""Historical background imports; implementation belongs to walk_background."""

from importlib import import_module

_EXPORTS = {
    "snapshot": "daengs_backend.services.walk_background.providers.area",
    "collect_area": "daengs_backend.services.walk_background.providers.area",
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
