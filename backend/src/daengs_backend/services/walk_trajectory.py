"""Historical public names; implementations belong to their explicit owner packages."""

from importlib import import_module

_EXPORTS = {
    "MAX_POINTS": "daengs_backend.services.walk_metrics.trajectory",
    "TrajectoryCalculation": "daengs_backend.services.walk_metrics.trajectory",
    "project": "daengs_backend.services.walk_metrics.trajectory",
    "walk_motion": "daengs_backend.services.walk_metrics.trajectory",
    "MotionConflict": "daengs_backend.services.walk_metrics.trajectory",
    "calculate": "daengs_backend.services.walk_metrics.trajectory",
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
