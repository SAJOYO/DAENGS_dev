"""Diary GET and generation orchestration; external writing follows a committed reservation."""

import asyncio
from datetime import UTC, datetime, timedelta

from daengs_backend.repositories import walk_storyboard as repo
from daengs_backend.schemas.walk_storyboard import DiaryStoryboardResponse
from daengs_backend.services.walk_diary.contracts import CardWritingResult
from daengs_backend.services.walk_diary.legacy.board_slots import complete_slot_board
from daengs_backend.services.walk_diary.legacy.bundle import write_diary
from daengs_backend.services.walk_diary.lifecycle.publication import (
    fallback,
    settle_expired,
    within_budget,
)
from daengs_backend.services.walk_diary.lifecycle.reservation import complete_diary, reserve_diary
from daengs_backend.services.walk_diary.lifecycle.snapshot import (
    apply_backgrounds,
    generation_revision,
    restore_backgrounds,
    result,
    snapshot,
)
from daengs_backend.services.walk_diary.runtime import write_board
from daengs_backend.services.walk_diary.storage.board import store_board
from daengs_backend.services.walk_diary.storage.bundle import store_diary
from daengs_backend.services.walk_diary.writing.assembly import complete_cards
from daengs_walk.diary_board_output import publish_board


async def get_diary(session, owner, walk_id, target, bundle_format="walk-diary-bundle-v1"):
    _, prepared, revision = await snapshot(session, owner, walk_id, target, bundle_format)
    row = await repo.current(session, walk_id)
    prepared = restore_backgrounds(prepared, row)
    revision = generation_revision(prepared, bundle_format)
    if prepared.board:
        settle_expired(prepared, row)
    value = result(prepared, row, revision)
    await session.commit()
    return value


async def generate_diary(
    session,
    owner,
    walk_id,
    request,
    *,
    writer=None,
    legacy_collector=None,
    legacy_context_wait=False,
):
    """Publish with the negotiated writer.

    legacy_collector explicitly opts historical writers into pre-reservation collection.
    Card providers/collection belong to write_board, which runs after reservation;
    wrapping a writer alone never changes collection timing. The historical entry-context
    grace period is opt-in via legacy_context_wait, independently of provider injection.
    """
    started = datetime.now(UTC)
    deadline = (
        started + timedelta(milliseconds=request.preparation_budget_ms)
        if request.preparation_budget_ms is not None
        else None
    )
    reservation = await reserve_diary(
        session,
        owner,
        walk_id,
        request,
        started=started,
        deadline=deadline,
        clock=lambda: datetime.now(UTC),
        legacy_collector=legacy_collector,
        legacy_context_wait=legacy_context_wait,
    )
    if isinstance(reservation, DiaryStoryboardResponse):
        return reservation
    prepared, revision = reservation.prepared, reservation.revision
    source, ticket = prepared.input.source, reservation.ticket
    collected = reservation.collected
    bundle, failure = None, None
    try:
        write = writer or (write_board if prepared.board else write_diary)
        writing_input = prepared.board if prepared.board else prepared.prepared
        output = (
            await within_budget(write, source, writing_input, deadline)
            if prepared.board and deadline is not None
            else await write(source, writing_input)
        )
        if prepared.board:
            if isinstance(output, CardWritingResult) and output.scene_backgrounds is not None:
                collected = output.scene_backgrounds
                prepared = apply_backgrounds(prepared, collected)
            completed = (
                complete_cards(prepared, output)
                if isinstance(output, CardWritingResult)
                else complete_slot_board(prepared, output)
            )
            bundle = store_board(prepared, completed, revision, writing=output)
        elif (
            output.input_revision != ticket.input_revision
            or output.plan_revision != prepared.prepared.plan.revision()
        ):
            raise ValueError("writer returned another generation's bundle")
        else:
            bundle = store_diary(prepared, output, revision)
    except asyncio.CancelledError:
        raise  # New publications retain their deadline/base; older requests retain the lease.
    except TimeoutError:
        if prepared.board:
            bundle = fallback(prepared, revision)
        else:
            failure = "diary_generation_failed"
    except Exception:  # noqa: BLE001 - never persist raw source/provider exception details
        if prepared.board:
            default_board = prepared.board.board.model_copy(
                update={"model_status": "unavailable", "failure_code": "provider_failed"}
            )
            bundle = store_board(
                prepared, publish_board(default_board, prepared.board.plan), revision
            )
        else:
            bundle, failure = None, "diary_generation_failed"
    return await complete_diary(
        session,
        owner,
        walk_id,
        request,
        reservation,
        bundle=bundle,
        failure=failure,
        collected=collected,
        clock=lambda: datetime.now(UTC),
    )
