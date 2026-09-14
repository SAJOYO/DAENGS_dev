"""Reserve and complete generations around external writing, preserving commit boundaries."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from daengs_backend.config import settings
from daengs_backend.repositories import walk_storyboard as repo
from daengs_backend.schemas.walk_storyboard import DiaryStoryboardResponse
from daengs_backend.services.walk_diary.guard import (
    GenerationTicket,
    StaleDiaryGeneration,
    bind_generation,
    require_current,
)
from daengs_backend.services.walk_diary.lifecycle.negotiation import guard_old_writer
from daengs_backend.services.walk_diary.lifecycle.publication import (
    preparation,
    publication_reservation,
    settle_expired,
)
from daengs_backend.services.walk_diary.lifecycle.snapshot import (
    apply_backgrounds,
    generation_revision,
    restore_backgrounds,
    result,
    revisions,
    snapshot,
)
from daengs_backend.services.walk_diary.preparation.diary import PreparedWalkDiary
from daengs_backend.services.walk_diary.storage.board import load_board
from daengs_backend.services.walk_storyboard_state import (
    LEASE_SECONDS,
    StoryboardConflict,
    complete,
    reserve,
    reusable,
)
from daengs_walk.diary.board.backgrounds import SceneBackgroundSnapshot
from daengs_walk.diary.board.output import BOARD_FORMAT

FIRST_BOARD_CONTEXT_GRACE = timedelta(minutes=10)


@dataclass(frozen=True)
class ReservedDiary:
    """Committed input handed to writing; never carry a live ORM row across external I/O."""

    prepared: PreparedWalkDiary
    revision: str
    ticket: GenerationTicket
    collected: SceneBackgroundSnapshot | None


async def reserve_diary(
    session,
    owner,
    walk_id,
    request,
    *,
    started: datetime,
    deadline: datetime | None,
    clock: Callable[[], datetime],
    legacy_collector=None,
    legacy_context_wait=False,
) -> ReservedDiary | DiaryStoryboardResponse:
    """Reuse/settle an existing publication or commit a new generation before writing.

    The explicitly selected legacy collector runs after releasing the first transaction;
    its result must be checked against freshly read input before reserving.
    Only legacy_context_wait opts into waiting for existing entry-context jobs.
    """
    principal, prepared, revision = await snapshot(
        session, owner, walk_id, request.target_scene_count, request.bundle_format
    )
    source = prepared.input.source
    if {str(k): v for k, v in request.expected_entries.items()} != revisions(source):
        raise StoryboardConflict("행동 기록이 변경됐어요. 기록을 다시 동기화해 주세요.")
    if request.expected_photo_manifest != source.photo_manifest:
        raise StoryboardConflict("사진 목록이 변경됐어요. 승인된 사진 버전을 확인해 주세요.")
    row = await repo.current(session, walk_id)
    prepared = restore_backgrounds(prepared, row)
    revision = generation_revision(prepared, request.bundle_format)
    if not prepared.board:
        guard_old_writer(row)
    else:
        settle_expired(prepared, row)
    now = clock()
    value = result(prepared, row, revision)
    uploaded_at = prepared.input.uploaded_at
    if (
        prepared.board
        and legacy_context_wait
        and row is None
        and settings.walk_entry_context_enabled
        and prepared.input.context_pending
        and uploaded_at is not None
        and uploaded_at.utcoffset() is not None
        and now < uploaded_at + FIRST_BOARD_CONTEXT_GRACE
    ):
        # Only explicit historical callers wait here; the default card graph owns its
        # bounded collection and must be free to start ready actions immediately.
        # Do not spend a generation or hold the Walk lock while collection runs.
        # The grace is fixed to server upload time, never attempts or retry timestamps.
        await session.commit()
        return value
    # A new-format client cannot take over another format's live generation lease.
    if (
        prepared.board
        and row is not None
        and row.status == "running"
        and (
            preparation(row) is not None
            and now < datetime.fromisoformat(preparation(row)["deadline_at"])
            or row.updated_at > now - timedelta(seconds=LEASE_SECONDS)
            and preparation(row) is None
        )
    ):
        await session.commit()
        return value.model_copy(update={"status": "running"})
    if (value.status == "ready" and not request.refresh) or (
        row is not None
        and row.status == "running"
        and reusable(row, revision, request.refresh, now)
    ):
        await session.commit()
        return value
    collected = None
    if prepared.board and settings.walk_diary_space_enabled and legacy_collector is not None:
        selected_board = prepared.board.board
        await session.commit()  # Public acquisition must never hold the Walk lock.
        remaining = (deadline - clock()).total_seconds() if deadline else 4.5
        if remaining > 0:
            try:
                async with asyncio.timeout(min(4.5, remaining)):
                    collected = await legacy_collector(selected_board)
            except TimeoutError:
                pass  # A bounded first publication can still use its original base materials.
        principal, prepared, revision = await snapshot(
            session, owner, walk_id, request.target_scene_count, request.bundle_format
        )
        source = prepared.input.source
        if collected is not None:
            try:
                prepared = apply_backgrounds(prepared, collected)
            except ValueError:
                raise StoryboardConflict(
                    "조회 중 산책 장면이 변경됐어요. 다시 생성해 주세요."
                ) from None
            revision = generation_revision(prepared, request.bundle_format)
        if {str(k): v for k, v in request.expected_entries.items()} != revisions(
            source
        ) or request.expected_photo_manifest != source.photo_manifest:
            raise StoryboardConflict("조회 중 산책 원본이 변경됐어요. 다시 동기화해 주세요.")
        row = await repo.current(session, walk_id)
        now = clock()
        settle_expired(prepared, row, now)
        value = result(prepared, row, revision)
        if (value.status == "ready" and not request.refresh) or (
            row is not None
            and row.status == "running"
            and (
                row.updated_at > now - timedelta(seconds=LEASE_SECONDS)
                or preparation(row) is not None
                and now < datetime.fromisoformat(preparation(row)["deadline_at"])
            )
        ):
            await session.commit()
            return value
    if prepared.board and row is not None and row.status == "ready":
        try:
            receipt = getattr(load_board(row.bundle), "writing_receipt", None)
            if hasattr(receipt, "result"):
                prepared = replace(
                    prepared,
                    board=replace(
                        prepared.board,
                        cached_jobs=tuple(j.model_dump(mode="json") for j in receipt.result.jobs),
                    ),
                )
        except ValueError:
            pass  # A damaged historical receipt is not a writing cache.
    generation = reserve(
        session,
        walk_id,
        row,
        revision,
        now,
        bundle_format=BOARD_FORMAT if prepared.board else None,
        pending_bundle=publication_reservation(prepared, revision, started, deadline)
        if prepared.board and deadline is not None
        else None,
    )
    ticket = bind_generation(principal, source, generation)
    await session.commit()  # Release the Walk lock/transaction before the LLM call.
    return ReservedDiary(prepared, revision, ticket, collected)


async def complete_diary(
    session,
    owner,
    walk_id,
    request,
    reservation: ReservedDiary,
    *,
    bundle,
    failure,
    collected: SceneBackgroundSnapshot | None,
    clock: Callable[[], datetime],
) -> DiaryStoryboardResponse:
    """Re-read under the Walk lock; a deadline GET or newer generation always wins."""
    principal, latest, latest_revision = await snapshot(
        session, owner, walk_id, request.target_scene_count, request.bundle_format
    )
    if collected is not None and latest.board:
        try:
            latest = apply_backgrounds(latest, collected)
            latest_revision = generation_revision(latest, request.bundle_format)
        except ValueError:
            pass  # A source change invalidates the generation; never rebind its collection.
    current = await repo.current(session, walk_id)
    if latest.board:
        settle_expired(latest, current)
    try:
        require_current(
            reservation.ticket, principal, latest.input.source, current.generation if current else 0
        )
    except StaleDiaryGeneration:
        pass
    else:
        complete(
            current,
            reservation.ticket.generation,
            reservation.revision,
            latest_revision,
            bundle,
            failure,
            clock(),
        )
    value = result(latest, current, latest_revision)
    await session.commit()
    return value
