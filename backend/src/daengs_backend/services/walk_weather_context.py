"""Historical background imports; implementation belongs to walk_background."""

from importlib import import_module

_EXPORTS = {
    "LOOKUP_TIMEOUT_S": "daengs_backend.services.walk_background.providers.weather",
    "outcome": "daengs_backend.services.walk_background.providers.weather",
    "collect_temperature": "daengs_backend.services.walk_background.providers.weather",
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
