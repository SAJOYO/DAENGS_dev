"""Historical public names; implementations belong to their explicit owner packages."""

from importlib import import_module

_EXPORTS = {
    "WalkMeasurement": "daengs_backend.services.walk_metrics.measurement",
    "WalkMeasurementChunk": "daengs_backend.services.walk_metrics.measurement",
    "repo": "daengs_backend.services.walk_metrics.measurement",
    "motion_repo": "daengs_backend.services.walk_metrics.measurement",
    "precision_repo": "daengs_backend.services.walk_metrics.measurement",
    "CHUNK_SIZE": "daengs_backend.services.walk_metrics.measurement",
    "MAX_POINTS": "daengs_backend.services.walk_metrics.measurement",
    "VERSION": "daengs_backend.services.walk_metrics.measurement",
    "ChunkRef": "daengs_backend.services.walk_metrics.measurement",
    "MeasurementSummary": "daengs_backend.services.walk_metrics.measurement",
    "RoutePage": "daengs_backend.services.walk_metrics.measurement",
    "RoutePoint": "daengs_backend.services.walk_metrics.measurement",
    "project_measurement": "daengs_backend.services.walk_metrics.measurement",
    "walk_motion": "daengs_backend.services.walk_metrics.measurement",
    "WalkNotFoundError": "daengs_backend.services.walk_metrics.measurement",
    "MotionUnavailable": "daengs_backend.services.walk_metrics.measurement",
    "MotionConflict": "daengs_backend.services.walk_metrics.measurement",
    "digest": "daengs_backend.services.walk_metrics.measurement",
    "sha": "daengs_backend.services.walk_metrics.measurement",
    "input_key": "daengs_backend.services.walk_metrics.measurement",
    "project": "daengs_backend.services.walk_metrics.measurement",
    "owned": "daengs_backend.services.walk_metrics.measurement",
    "checked": "daengs_backend.services.walk_metrics.measurement",
    "prepare": "daengs_backend.services.walk_metrics.measurement",
    "read": "daengs_backend.services.walk_metrics.measurement",
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
