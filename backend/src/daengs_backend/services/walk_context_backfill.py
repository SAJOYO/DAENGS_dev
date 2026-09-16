"""Historical public names; implementation belongs to the records/photo owner package."""

from importlib import import_module

_EXPORTS = {
    "BACKFILL_POLICY": "daengs_backend.services.walk_records.backfill",
    "TAGS": "daengs_backend.services.walk_records.backfill",
    "USABLE": "daengs_backend.services.walk_records.backfill",
    "MISSING": "daengs_backend.services.walk_records.backfill",
    "MAX_SOURCES": "daengs_backend.services.walk_records.backfill",
    "StaleBackfillPlan": "daengs_backend.services.walk_records.backfill",
    "enabled": "daengs_backend.services.walk_records.backfill",
    "location_reason": "daengs_backend.services.walk_records.backfill",
    "disposition": "daengs_backend.services.walk_records.backfill",
    "build_plan": "daengs_backend.services.walk_records.backfill",
    "run": "daengs_backend.services.walk_records.backfill",
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
