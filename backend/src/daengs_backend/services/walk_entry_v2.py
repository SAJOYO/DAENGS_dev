"""Historical public names; implementation belongs to the records/photo owner package."""

from importlib import import_module

_EXPORTS = {
    "EntryConflict": "daengs_backend.services.walk_records.errors",
    "EntryDeleted": "daengs_backend.services.walk_records.errors",
    "EntryInvalid": "daengs_backend.services.walk_records.errors",
    "EntryNotFound": "daengs_backend.services.walk_records.errors",
    "EntryUpgradeRequired": "daengs_backend.services.walk_records.errors",
    "EntryWritesDisabled": "daengs_backend.services.walk_records.errors",
    "validate_new_pin": "daengs_backend.services.walk_records.pins",
    "validate_sources": "daengs_backend.services.walk_records.pins",
    "validate_transition": "daengs_backend.services.walk_records.pins",
    "capabilities": "daengs_backend.services.walk_records.policy",
    "guard_v1": "daengs_backend.services.walk_records.policy",
    "require_enabled": "daengs_backend.services.walk_records.policy",
    "build_profile": "daengs_backend.services.walk_records.profile",
    "legacy_pin": "daengs_backend.services.walk_records.v2",
    "response": "daengs_backend.services.walk_records.v2",
    "digest": "daengs_backend.services.walk_records.v2",
    "request_payload": "daengs_backend.services.walk_records.v2",
    "list_entries": "daengs_backend.services.walk_records.v2",
    "locked": "daengs_backend.services.walk_records.v2",
    "replay": "daengs_backend.services.walk_records.v2",
    "compare_revision": "daengs_backend.services.walk_records.v2",
    "store": "daengs_backend.services.walk_records.v2",
    "write": "daengs_backend.services.walk_records.v2",
    "finalize_pin": "daengs_backend.services.walk_records.v2",
    "validate_recording_receipt": "daengs_backend.services.walk_records.v2",
    "remove": "daengs_backend.services.walk_records.v2",
    "profile": "daengs_backend.services.walk_records.v2",
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
