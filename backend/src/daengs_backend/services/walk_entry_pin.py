"""Historical public names; implementation belongs to the records/photo owner package."""

from importlib import import_module

_EXPORTS = {
    "EntryConflict": "daengs_backend.services.walk_records.errors",
    "EntryInvalid": "daengs_backend.services.walk_records.errors",
    "POLICY": "daengs_backend.services.walk_records.pins",
    "ALGORITHM": "daengs_backend.services.walk_records.pins",
    "same_point": "daengs_backend.services.walk_records.pins",
    "validate_sources": "daengs_backend.services.walk_records.pins",
    "validate_new_pin": "daengs_backend.services.walk_records.pins",
    "validate_transition": "daengs_backend.services.walk_records.pins",
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
