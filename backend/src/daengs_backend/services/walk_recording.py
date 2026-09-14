"""Historical public names; implementation belongs to the session owner package."""

from importlib import import_module

_EXPORTS = {
    "RECORDING_POLICY": "daengs_backend.services.walk_session.chunk",
    "decode_chunk": "daengs_backend.services.walk_session.chunk",
    "encode_chunk": "daengs_backend.services.walk_session.chunk",
    "walk_input_fingerprint": "daengs_backend.services.walk_session.finalize",
    "RecordingConflict": "daengs_backend.services.walk_session.recording",
    "recording_receipt": "daengs_backend.services.walk_session.recording",
    "merge_recording_points": "daengs_backend.services.walk_session.recording",
    "repair_recording": "daengs_backend.services.walk_session.recording",
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
