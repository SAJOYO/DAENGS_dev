"""Version availability and legacy access policy shared by readers and writers."""

from daengs_backend.config import settings
from daengs_backend.repositories import walk_entry_v2 as repo
from daengs_backend.services.walk_entry_errors import EntryNotFound, EntryUpgradeRequired
from daengs_backend.services.walk_entry_pin import POLICY


def require_enabled():
    if not settings.walk_entry_v2_enabled:
        raise EntryNotFound


def capabilities():
    enabled = settings.walk_entry_v2_enabled
    writing = enabled and settings.walk_entry_v2_write_enabled
    return {
        "read_versions": ["walk-entry-v1"] + (["walk-entry-v2"] if enabled else []),
        "write_versions": ["walk-entry-v1"] + (["walk-entry-v2"] if writing else []),
        "active_policy_versions": [POLICY] if writing else [],
        "pin_observation_cutoff_supported": enabled,
        "gps_recording_versions": ["gps-recording-v1"],
        "storyboard_formats": ["walk-storyboard-candidates-v5"] if enabled else [],
        "entry_context_versions": ["walk-entry-context-v2"] if enabled else [],
    }


async def guard_v1(session, walk_ids, *, entry_id=None):
    if settings.walk_entry_v2_enabled and await repo.contains_v2(
        session, walk_ids, entry_id=entry_id
    ):
        raise EntryUpgradeRequired
