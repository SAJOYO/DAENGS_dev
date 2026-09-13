"""A bounded first publication on the existing JSONB reservation, without another worker."""

import asyncio
from datetime import UTC, datetime

from daengs_backend.services.walk_diary_board_storage import (
    load_board,
    source_revision,
    store_board,
)
from daengs_backend.services.walk_storyboard_state import complete
from daengs_walk.diary_board_output import BOARD_FORMAT, publish_board

FORMAT = "walk-diary-preparation-v1"
_discarded_tasks = set()


def fallback(prepared, revision, code="budget_exceeded"):
    base = prepared.board.board.model_copy(
        update={"model_status": "unavailable", "failure_code": code}
    )
    return store_board(prepared, publish_board(base, prepared.board.plan), revision)


def publication_reservation(prepared, revision, started, deadline):
    return {
        "format": FORMAT,
        "bundle_format": BOARD_FORMAT,
        "started_at": started.isoformat(),
        "deadline_at": deadline.isoformat(),
        "fallback": fallback(prepared, revision),
    }


def preparation(row):
    raw = row.bundle if row is not None and row.status == "running" else None
    return raw if isinstance(raw, dict) and raw.get("format") == FORMAT else None


def settle_expired(prepared, row, now=None):
    raw = preparation(row)
    if raw is None:
        return False
    now = now or datetime.now(UTC)
    if now < datetime.fromisoformat(raw["deadline_at"]):
        return False
    receipt = load_board(raw["fallback"])
    if (
        receipt.generation_revision != row.input_revision
        or receipt.bundle.client_session_id != prepared.input.source.client_session_id
        or receipt.source_revision != source_revision(prepared)
    ):
        return False  # Original source edits never authorize publishing an old snapshot.
    return complete(
        row,
        row.generation,
        row.input_revision,
        row.input_revision,
        receipt.model_dump(mode="json"),
        None,
        now,
    )


def _consume(task):
    _discarded_tasks.discard(task)
    if not task.cancelled():
        task.exception()


async def within_budget(write, source, prepared, deadline):
    from daengs_backend.services.walk_diary_deadline import publication_deadline

    remaining = (deadline - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        raise TimeoutError
    token = publication_deadline.set(deadline)
    try:
        task = asyncio.create_task(write(source, prepared))
    finally:
        publication_deadline.reset(token)
    try:
        done, _ = await asyncio.wait({task}, timeout=remaining)
        if not done or datetime.now(UTC) >= deadline:
            raise TimeoutError
        return task.result()
    finally:
        if not task.done():
            # A provider may swallow cancellation. Its late value has no publication authority.
            _discarded_tasks.add(task)
            task.add_done_callback(_consume)
            task.cancel()
        elif not task.cancelled():
            task.exception()
