"""Historical public names; implementations belong to their explicit owner packages."""

from importlib import import_module

_EXPORTS = {
    "CHUNK_SIZE": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "MotionManifest": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "MotionObservation": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "Point": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "replay": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "walk_input_fingerprint": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "MotionConflict": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "chunk_digest": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "evidence_digest": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "manifest_digest": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "Contract": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "EvidenceJournal": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "IntervalAssessment": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "IntervalLedger": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "JournalEvent": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "PointAssessment": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "SourceRange": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "SourceRef": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "PathSection": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "TrajectoryBoundaries": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "TrajectoryLocation": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "boundaries": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "path_sections": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "MeasurementKey": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "MeasurementSnapshot": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "WalkScope": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "digest": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "BAD_TIME": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "ObservedConnectionPolicy": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "DEFAULT_CONNECTION_POLICY": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "ShadowResult": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "ShadowAssembler": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "backup_fingerprint": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "replay_shadow": "daengs_backend.services.walk_metrics.trajectory_shadow",
    "calculate_shadow": "daengs_backend.services.walk_metrics.trajectory_shadow",
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
