"""Historical public names; implementations belong to their explicit owner packages."""

from importlib import import_module

_EXPORTS = {
    "settings": "daengs_backend.services.walk_legacy.context",
    "KINDS": "daengs_backend.services.walk_legacy.context",
    "LABELS": "daengs_backend.services.walk_legacy.context",
    "unavailable_contexts": "daengs_backend.services.walk_legacy.context",
    "lookup_contexts": "daengs_backend.services.walk_legacy.context",
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
