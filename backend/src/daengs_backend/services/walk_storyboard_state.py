"""Shared row/lease transitions for existing storyboard and opt-in diary generation."""

from datetime import timedelta

from daengs_backend.models.walk_storyboard import WalkStoryboard

LEASE_SECONDS = 60


class StoryboardNotFound(LookupError):
    pass


class StoryboardConflict(ValueError):
    pass


def reusable(row, revision, refresh, now, lease_seconds=LEASE_SECONDS):
    return (
        row is not None
        and row.input_revision == revision
        and (
            (row.status == "running" and row.updated_at > now - timedelta(seconds=lease_seconds))
            or (row.status == "ready" and not refresh)
        )
    )


def reserve(session, walk_id, row, revision, now):
    generation = (row.generation if row else 0) + 1
    if row is None:
        row = WalkStoryboard(walk_id=walk_id)
        session.add(row)
    row.generation, row.input_revision = generation, revision
    row.status, row.updated_at, row.error_code = "running", now, None
    row.bundle = None
    return generation


def complete(row, generation, reserved_revision, latest_revision, bundle, failure, now):
    if (
        row is None
        or row.generation != generation
        or row.status != "running"
        or row.input_revision != reserved_revision
        or latest_revision != reserved_revision
    ):
        return False
    row.status = "failed" if failure else "ready"
    row.bundle, row.error_code, row.updated_at = bundle, failure, now
    return True
