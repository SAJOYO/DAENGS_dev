"""Historical public names; implementations belong to their explicit owner packages."""

from importlib import import_module

_EXPORTS = {
    "settings": "daengs_backend.services.walk_legacy.titles",
    "StoryboardBundleV3": "daengs_backend.services.walk_legacy.titles",
    "StoryboardBundleV4": "daengs_backend.services.walk_legacy.titles",
    "StoryboardBundleV5": "daengs_backend.services.walk_legacy.titles",
    "StrictModel": "daengs_backend.services.walk_legacy.titles",
    "fingerprint": "daengs_backend.services.walk_legacy.titles",
    "MODEL": "daengs_backend.services.walk_legacy.titles",
    "TIMEOUT_SECONDS": "daengs_backend.services.walk_legacy.titles",
    "MAX_INPUT_BYTES": "daengs_backend.services.walk_legacy.titles",
    "PROMPT": "daengs_backend.services.walk_legacy.titles",
    "Heading": "daengs_backend.services.walk_legacy.titles",
    "SceneHeading": "daengs_backend.services.walk_legacy.titles",
    "Headings": "daengs_backend.services.walk_legacy.titles",
    "title_input": "daengs_backend.services.walk_legacy.titles",
    "generate_headings": "daengs_backend.services.walk_legacy.titles",
    "title_storyboard": "daengs_backend.services.walk_legacy.titles",
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
