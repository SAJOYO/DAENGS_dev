"""Historical public names; implementation belongs to the session owner package."""

from importlib import import_module

_EXPORTS = {
    "MotionConflict": "daengs_backend.services.walk_session.motion_contract",
    "digest": "daengs_backend.services.walk_session.motion_contract",
    "manifest_digest": "daengs_backend.services.walk_session.motion_contract",
    "chunk_digest": "daengs_backend.services.walk_session.motion_contract",
    "evidence_digest": "daengs_backend.services.walk_session.motion_contract",
    "validate_manifest": "daengs_backend.services.walk_session.motion_contract",
    "validate_observations": "daengs_backend.services.walk_session.motion_contract",
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
