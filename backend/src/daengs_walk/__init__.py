"""Walk domain compatibility exports.

Use contracts/evidence for measurement, cellophane/capsule for sealed outputs,
and spatial_diary for queries. Importing the package does not load these owners.
"""

from importlib import import_module

__all__ = [
    "Cellophane",
    "SpatialDiaryViewSpec",
    "WalkCapsuleArtifacts",
    "WalkEvidenceBundle",
    "WalkEvidencePoint",
    "aggregate_spatial_field",
    "analyze_walk",
    "build_cellophane",
    "build_walk_capsule",
    "context_facets",
    "select_context_anchor",
]

_EXPORTS = {
    "WalkCapsuleArtifacts": "daengs_walk.capsule",
    "build_walk_capsule": "daengs_walk.capsule",
    "select_context_anchor": "daengs_walk.capsule",
    "Cellophane": "daengs_walk.cellophane",
    "build_cellophane": "daengs_walk.cellophane",
    "WalkEvidencePoint": "daengs_walk.contracts",
    "WalkEvidenceBundle": "daengs_walk.evidence",
    "analyze_walk": "daengs_walk.evidence",
    "SpatialDiaryViewSpec": "daengs_walk.spatial_diary",
    "aggregate_spatial_field": "daengs_walk.spatial_diary",
    "context_facets": "daengs_walk.spatial_diary",
}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_EXPORTS[name]), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
