"""Historical public names; implementations belong to their explicit owner packages."""

from importlib import import_module

_EXPORTS = {
    "MotionManifest": "daengs_backend.services.walk_metrics.motion_engine",
    "MotionObservation": "daengs_backend.services.walk_metrics.motion_engine",
    "validate_manifest": "daengs_backend.services.walk_metrics.motion_engine",
    "validate_observations": "daengs_backend.services.walk_metrics.motion_engine",
    "Point": "daengs_backend.services.walk_metrics.motion_engine",
    "Estimator": "daengs_backend.services.walk_metrics.motion_engine",
    "Segments": "daengs_backend.services.walk_metrics.motion_engine",
    "replay": "daengs_backend.services.walk_metrics.motion_engine",
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
