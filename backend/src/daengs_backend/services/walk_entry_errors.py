"""Historical public names; implementation belongs to the records/photo owner package."""

from importlib import import_module

_EXPORTS = {
    "EntryNotFound": "daengs_backend.services.walk_records.errors",
    "EntryConflict": "daengs_backend.services.walk_records.errors",
    "EntryInvalid": "daengs_backend.services.walk_records.errors",
    "EntryDeleted": "daengs_backend.services.walk_records.errors",
    "EntryWritesDisabled": "daengs_backend.services.walk_records.errors",
    "EntryUpgradeRequired": "daengs_backend.services.walk_records.errors",
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
