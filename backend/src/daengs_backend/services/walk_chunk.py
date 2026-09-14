"""Historical public names; implementation belongs to the session owner package."""

from importlib import import_module

_EXPORTS = {
    "CHUNK_VERSION": "daengs_backend.services.walk_session.chunk",
    "RECORDING_CHUNK_VERSION": "daengs_backend.services.walk_session.chunk",
    "RECORDING_POLICY": "daengs_backend.services.walk_session.chunk",
    "CHUNK_COLUMNS": "daengs_backend.services.walk_session.chunk",
    "encode_chunk": "daengs_backend.services.walk_session.chunk",
    "decode_chunk": "daengs_backend.services.walk_session.chunk",
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
