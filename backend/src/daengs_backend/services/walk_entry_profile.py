"""Historical public names; implementation belongs to the records/photo owner package."""

from importlib import import_module

_EXPORTS = {"build_profile": "daengs_backend.services.walk_records.profile"}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_EXPORTS[name]), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
