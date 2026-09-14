"""Historical public names; implementation belongs to the session owner package."""

from importlib import import_module

_EXPORTS = {
    "WalkNotFoundError": "daengs_backend.services.walk_session.errors",
    "decode_chunk": "daengs_backend.services.walk_session.chunk",
    "walk_input_fingerprint": "daengs_backend.services.walk_session.finalize",
    "MotionConflict": "daengs_backend.services.walk_session.motion_contract",
    "chunk_digest": "daengs_backend.services.walk_session.motion_contract",
    "evidence_digest": "daengs_backend.services.walk_session.motion_contract",
    "manifest_digest": "daengs_backend.services.walk_session.motion_contract",
    "validate_manifest": "daengs_backend.services.walk_session.motion_contract",
    "validate_observations": "daengs_backend.services.walk_session.motion_contract",
    "MotionUnavailable": "daengs_backend.services.walk_session.motion",
    "begin": "daengs_backend.services.walk_session.motion",
    "upload_chunk": "daengs_backend.services.walk_session.motion",
    "completed_input": "daengs_backend.services.walk_session.motion",
    "complete": "daengs_backend.services.walk_session.motion",
    "read": "daengs_backend.services.walk_session.motion",
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
