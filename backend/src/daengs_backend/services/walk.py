"""Historical public names; implementation belongs to the session owner package."""

from importlib import import_module

_EXPORTS = {
    "encode_chunk": "daengs_backend.services.walk_session.chunk",
    "PreparedWalkEvidence": "daengs_backend.services.walk_session.finalize",
    "prepare_finalized_walk": "daengs_backend.services.walk_session.finalize",
    "WalkNotFoundError": "daengs_backend.services.walk_session.errors",
    "WalkStateConflictError": "daengs_backend.services.walk_session.errors",
    "list_walks": "daengs_backend.services.walk_session.lifecycle",
    "get_walk": "daengs_backend.services.walk_session.lifecycle",
    "upload_walk": "daengs_backend.services.walk_session.lifecycle",
    "append_points": "daengs_backend.services.walk_session.lifecycle",
    "finalize_walk": "daengs_backend.services.walk_session.lifecycle",
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
