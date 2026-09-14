"""Historical public names; implementations belong to their explicit owner packages."""

from importlib import import_module

_EXPORTS = {
    "WalkCellophaneSheet": "daengs_backend.services.walk_views.spatial_diary",
    "pet_repo": "daengs_backend.services.walk_views.spatial_diary",
    "diary_repo": "daengs_backend.services.walk_views.spatial_diary",
    "decode_stored_cellophane": "daengs_backend.services.walk_artifacts.cellophane",
    "CAPSULE_VERSION": "daengs_backend.services.walk_views.spatial_diary",
    "TrailContextSnapshot": "daengs_backend.services.walk_views.spatial_diary",
    "CANONICAL_PAINT_SPEC": "daengs_backend.services.walk_views.spatial_diary",
    "Cellophane": "daengs_backend.services.walk_views.spatial_diary",
    "PaintSpec": "daengs_backend.services.walk_views.spatial_diary",
    "MixedPaintGenerationError": "daengs_backend.services.walk_views.spatial_diary",
    "SpatialDiaryViewReceipt": "daengs_backend.services.walk_views.spatial_diary",
    "SpatialDiaryViewSpec": "daengs_backend.services.walk_views.spatial_diary",
    "SpatialField": "daengs_backend.services.walk_views.spatial_diary",
    "aggregate_spatial_field": "daengs_backend.services.walk_views.spatial_diary",
    "build_view_receipt": "daengs_backend.services.walk_views.spatial_diary",
    "context_is_known": "daengs_backend.services.walk_views.spatial_diary",
    "matches_walk_selector": "daengs_backend.services.walk_views.spatial_diary",
    "MAX_CANDIDATE_CAPSULES": "daengs_backend.services.walk_views.spatial_diary",
    "MAX_SELECTED_CAPSULES": "daengs_backend.services.walk_views.spatial_diary",
    "MAX_RAW_CELLS": "daengs_backend.services.walk_views.spatial_diary",
    "MAX_RESULT_CELLS": "daengs_backend.services.walk_views.spatial_diary",
    "SpatialDiaryPetNotFoundError": "daengs_backend.services.walk_views.spatial_diary",
    "IncompleteSpatialDiaryCapsuleError": "daengs_backend.services.walk_views.spatial_diary",
    "SpatialDiaryViewTooLargeError": "daengs_backend.services.walk_views.spatial_diary",
    "CapsuleIndex": "daengs_backend.services.walk_views.spatial_diary",
    "SpatialDiaryViewResult": "daengs_backend.services.walk_views.spatial_diary",
    "WalkRecordSheet": "daengs_backend.services.walk_views.spatial_diary",
    "query_record_sheets": "daengs_backend.services.walk_views.spatial_diary",
    "query_view": "daengs_backend.services.walk_views.spatial_diary",
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
