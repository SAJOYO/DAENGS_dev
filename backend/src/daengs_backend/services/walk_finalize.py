"""Historical public names; implementation belongs to the session owner package."""

from importlib import import_module

_EXPORTS = {
    "decode_chunk": "daengs_backend.services.walk_session.chunk",
    "INPUT_FINGERPRINT_VERSION": "daengs_backend.services.walk_session.finalize",
    "FinalizeInputErrorCode": "daengs_backend.services.walk_session.finalize",
    "StoredWalkPointChunk": "daengs_backend.services.walk_session.finalize",
    "FinalizeInputError": "daengs_backend.services.walk_session.finalize",
    "PreparedWalkEvidence": "daengs_backend.services.walk_session.finalize",
    "prepare_finalized_walk": "daengs_backend.services.walk_session.finalize",
    "walk_input_fingerprint": "daengs_backend.services.walk_session.finalize",
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
