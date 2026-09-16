"""Historical public names; implementation belongs to the records/photo owner package."""

from importlib import import_module

_EXPORTS = {
    "EntryNotFound": "daengs_backend.services.walk_records.errors",
    "guard_v1": "daengs_backend.services.walk_records.policy",
    "require_enabled": "daengs_backend.services.walk_records.policy",
    "reserve": "daengs_backend.services.walk_records.context",
    "reserve_pin": "daengs_backend.services.walk_records.context",
    "read": "daengs_backend.services.walk_records.context",
    "take": "daengs_backend.services.walk_records.context",
    "finish": "daengs_backend.services.walk_records.context",
    "process": "daengs_backend.services.walk_records.context",
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
