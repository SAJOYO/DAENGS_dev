"""Keep a saved board's format; negotiation is not an instruction to replace it."""

from daengs_backend.repositories import walk as walks
from daengs_backend.repositories import walk_storyboard as repo
from daengs_backend.services.walk_storyboard_state import StoryboardConflict, StoryboardNotFound
from daengs_walk.diary_board_output import BOARD_FORMAT


def stored_format(row):
    raw = row.bundle if row is not None else None
    if not isinstance(raw, dict):
        return None
    kind = raw.get("format")
    if kind in {"walk-diary-reservation-v1", "walk-diary-preparation-v1"}:
        return raw.get("bundle_format")
    if kind == "walk-diary-board-storage-v1":
        return BOARD_FORMAT
    if kind == "walk-diary-storage-v1":
        return "walk-diary-bundle-v1"
    return kind


def guard_old_writer(row):
    if stored_format(row) == BOARD_FORMAT:
        raise StoryboardConflict("새 산책 장면을 보려면 앱을 업데이트해 주세요.")


async def existing_format(session, owner, walk_id, target):
    from daengs_backend.config import settings

    if not settings.walk_diary_enabled:
        raise StoryboardNotFound
    # Ownership is checked before format/receipt metadata can influence a response.
    if await walks.get_owned_for_update(session, owner, walk_id) is None:
        raise StoryboardNotFound
    row = await repo.current(session, walk_id)
    stored = stored_format(row)
    if stored == "walk-diary-bundle-v1":
        counts = row.bundle.get("preparation_counts", {})
        return stored, counts.get("target", target)
    if stored in {f"walk-storyboard-candidates-v{n}" for n in range(1, 6)}:
        return stored, None
    return BOARD_FORMAT, target
