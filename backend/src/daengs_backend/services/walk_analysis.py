"""Historical public names; implementations belong to their explicit owner packages."""

from importlib import import_module

_EXPORTS = {
    "WalkAnalysis": "daengs_backend.services.walk_metrics.analysis",
    "PreparedWalkEvidence": "daengs_backend.services.walk_metrics.analysis",
    "MICRO_OBSERVATION_VERSION": "daengs_backend.services.walk_metrics.analysis",
    "CanonicalWalkFacts": "daengs_backend.services.walk_metrics.analysis",
    "MeasurementReceipt": "daengs_backend.services.walk_metrics.analysis",
    "MicroObservation": "daengs_backend.services.walk_metrics.analysis",
    "MotionEventOccurrence": "daengs_backend.services.walk_metrics.analysis",
    "WalkEvidenceBundle": "daengs_backend.services.walk_metrics.analysis",
    "WalkAnalysisPayloads": "daengs_backend.services.walk_metrics.analysis",
    "DecodedWalkAnalysis": "daengs_backend.services.walk_metrics.analysis",
    "build_analysis_model": "daengs_backend.services.walk_metrics.analysis",
    "analysis_payloads": "daengs_backend.services.walk_metrics.analysis",
    "decode_analysis_model": "daengs_backend.services.walk_metrics.analysis",
    "CELLOPHANE_SHEET_SCHEMA_VERSION": "daengs_backend.services.walk_artifacts.cellophane",
    "CELLOPHANE_CELL_COLUMNS": "daengs_backend.services.walk_artifacts.cellophane",
    "encode_cellophane": "daengs_backend.services.walk_artifacts.cellophane",
    "decode_cellophane": "daengs_backend.services.walk_artifacts.cellophane",
    "decode_stored_cellophane": "daengs_backend.services.walk_artifacts.cellophane",
    "cellophane_sheet_fingerprint": "daengs_backend.services.walk_artifacts.cellophane",
    "build_analysis_models": "daengs_backend.services.walk_artifacts.api",
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
