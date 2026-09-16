"""Historical public names; implementation belongs to the session owner package."""

from importlib import import_module

_EXPORTS = {
    "WalkNotFoundError": "daengs_backend.services.walk_session.errors",
    "decode_chunk": "daengs_backend.services.walk_session.chunk",
    "MotionConflict": "daengs_backend.services.walk_session.motion_contract",
    "chunk_digest": "daengs_backend.services.walk_session.precision_contract",
    "evidence_digest": "daengs_backend.services.walk_session.precision_contract",
    "manifest_digest": "daengs_backend.services.walk_session.precision_contract",
    "refine_points": "daengs_backend.services.walk_session.precision_contract",
    "begin": "daengs_backend.services.walk_session.precision",
    "upload": "daengs_backend.services.walk_session.precision",
    "complete": "daengs_backend.services.walk_session.precision",
    "refine_completed": "daengs_backend.services.walk_session.precision",
    "read": "daengs_backend.services.walk_session.precision",
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
