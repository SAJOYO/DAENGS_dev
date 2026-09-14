"""Historical public names; implementations belong to their explicit owner packages."""

from importlib import import_module

_EXPORTS = {
    "MotionConflict": "daengs_backend.services.walk_metrics.measurement_projection",
    "ObservedConnectionPolicy": "daengs_backend.services.walk_metrics.measurement_projection",
    "backup_fingerprint": "daengs_backend.services.walk_metrics.measurement_projection",
    "replay_shadow": "daengs_backend.services.walk_metrics.measurement_projection",
    "LedgerMetrics": "daengs_backend.services.walk_metrics.measurement_projection",
    "PathSection": "daengs_backend.services.walk_metrics.measurement_projection",
    "SourceWallTime": "daengs_backend.services.walk_metrics.measurement_projection",
    "TrajectoryBoundaries": "daengs_backend.services.walk_metrics.measurement_projection",
    "TrajectoryLocation": "daengs_backend.services.walk_metrics.measurement_projection",
    "MeasurementSnapshot": "daengs_backend.services.walk_metrics.measurement_projection",
    "MeasurementProjection": "daengs_backend.services.walk_metrics.measurement_projection",
    "project": "daengs_backend.services.walk_metrics.measurement_projection",
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
