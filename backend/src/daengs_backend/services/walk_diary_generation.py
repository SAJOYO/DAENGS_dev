"""Opt-in diary branch of the existing WalkStoryboard lifecycle; no second queue/table."""

import asyncio
from datetime import UTC, datetime, timedelta

from daengs_backend.config import settings
from daengs_backend.orchestration.contracts import PrincipalContext
from daengs_backend.repositories import walk_storyboard as repo
from daengs_backend.schemas.walk_storyboard import DiaryStoryboardResponse
from daengs_backend.services.walk_diary_base_board import prepare_saved_base_board
from daengs_backend.services.walk_diary_board_storage import read_board, store_board
from daengs_backend.services.walk_diary_board_writing import complete_board
from daengs_backend.services.walk_diary_contract import (
    StaleDiaryGeneration,
    bind_generation,
    require_current,
)
from daengs_backend.services.walk_diary_negotiation import guard_old_writer
from daengs_backend.services.walk_diary_prepare import PreparedWalkDiary, prepare_saved_diary
from daengs_backend.services.walk_diary_publication import (
    fallback,
    preparation,
    publication_reservation,
    settle_expired,
    within_budget,
)
from daengs_backend.services.walk_diary_storage import read_diary, store_diary
from daengs_backend.services.walk_diary_writing import write_diary, writing_version
from daengs_backend.services.walk_storyboard_state import (
    LEASE_SECONDS,
    StoryboardConflict,
    StoryboardNotFound,
    complete,
    reserve,
    reusable,
)
from daengs_walk.diary_board import BaseBoardPolicy
from daengs_walk.diary_board_output import BOARD_FORMAT, BOARD_RESPONSE, publish_board
from daengs_walk.diary_input import digest
from daengs_walk.diary_stamps import StampPolicy

FIRST_BOARD_CONTEXT_GRACE = timedelta(minutes=10)


async def snapshot(session, owner, walk_id, target, bundle_format="walk-diary-bundle-v1"):
    if not settings.walk_diary_enabled:
        raise StoryboardNotFound
    if target is None:
        raise StoryboardConflict("일기 장면 목표 수를 지정해 주세요.")
    principal = PrincipalContext(kind="APP_USER", subject=str(owner))
    try:
        policy = StampPolicy(target_scene_count=target)
        if bundle_format == BOARD_FORMAT:
            base = await prepare_saved_base_board(
                session, principal, walk_id, BaseBoardPolicy(intermediate=policy)
            )
            prepared = PreparedWalkDiary(base.input, base.plan.intermediate, base)
        else:
            prepared = await prepare_saved_diary(session, principal, walk_id, policy)
    except LookupError:
        raise StoryboardNotFound from None
    except ValueError:
        raise StoryboardConflict(
            "일기 원본을 준비할 수 없습니다. 기록 동기화를 확인해 주세요."
        ) from None
    revision = digest(
        {
            "format": bundle_format,
            "plan": prepared.board.plan.revision()
            if prepared.board
            else prepared.prepared.plan.revision(),
            "writer": writing_version(),
        }
    )
    return principal, prepared, revision


def revisions(source):
    return {r.ref.id: int(r.ref.version) for r in source.records if r.ref.store == "walk_entry"}


def result(prepared, row, revision):
    source = prepared.input.source
    state = "pending" if row is None else "stale" if row.input_revision != revision else row.status
    bundle, error = None, row.error_code if row is not None and state == "failed" else None
    preparation = prepared.board.plan if prepared.board else prepared.prepared
    counts, limits = preparation.counts, preparation.limits
    background_update = False
    if row is not None and row.status == "ready":
        try:
            stored = (read_board if prepared.board else read_diary)(prepared, row, revision)
            if stored is not None:
                state, bundle = "ready", stored.bundle
                counts, limits = stored.preparation_counts, stored.preparation_limits
                background_update = row.input_revision != revision
            else:
                state = "stale"
        except ValueError:
            state, error, bundle = "failed", "invalid_stored_diary", None
    return DiaryStoryboardResponse(
        format=BOARD_RESPONSE if prepared.board else "walk-diary-response-v1",
        session_id=source.client_session_id,
        generation=row.generation if row else 0,
        input_revision=row.input_revision if bundle is not None else revision,
        status=state,
        entry_revisions=revisions(source),
        photos_status=source.photos_status,
        photo_manifest=source.photo_manifest,
        target_scene_count=counts["target"],
        preparation_counts=counts,
        preparation_limits=limits,
        background_update_available=background_update,
        bundle=bundle,
        error_code=error,
    )


async def get_diary(session, owner, walk_id, target, bundle_format="walk-diary-bundle-v1"):
    _, prepared, revision = await snapshot(session, owner, walk_id, target, bundle_format)
    row = await repo.current(session, walk_id)
    if prepared.board:
        settle_expired(prepared, row)
    value = result(prepared, row, revision)
    await session.commit()
    return value


async def generate_diary(session, owner, walk_id, request, *, writer=None):
    started = datetime.now(UTC)
    deadline = (
        started + timedelta(milliseconds=request.preparation_budget_ms)
        if request.preparation_budget_ms is not None
        else None
    )
    principal, prepared, revision = await snapshot(
        session, owner, walk_id, request.target_scene_count, request.bundle_format
    )
    source = prepared.input.source
    if {str(k): v for k, v in request.expected_entries.items()} != revisions(source):
        raise StoryboardConflict("행동 기록이 변경됐어요. 기록을 다시 동기화해 주세요.")
    if request.expected_photo_manifest != source.photo_manifest:
        raise StoryboardConflict("사진 목록이 변경됐어요. 승인된 사진 버전을 확인해 주세요.")
    row = await repo.current(session, walk_id)
    if not prepared.board:
        guard_old_writer(row)
    else:
        settle_expired(prepared, row)
    now = datetime.now(UTC)
    value = result(prepared, row, revision)
    uploaded_at = prepared.input.uploaded_at
    if (
        prepared.board
        and row is None
        and settings.walk_entry_context_enabled
        and prepared.input.context_pending
        and uploaded_at is not None
        and uploaded_at.utcoffset() is not None
        and now < uploaded_at + FIRST_BOARD_CONTEXT_GRACE
    ):
        # The existing client retries pending responses. Do not spend a generation or
        # hold the Walk lock while collection runs. A stalled worker cannot extend the
        # deadline: it is fixed to server upload time, not attempts or retry timestamps.
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
    bundle, failure = None, None
    try:
        output = (
            await within_budget(writer or write_diary, source, prepared.prepared, deadline)
            if prepared.board and deadline is not None
            else await (writer or write_diary)(source, prepared.prepared)
        )
        if (
            output.input_revision != ticket.input_revision
            or output.plan_revision != prepared.prepared.plan.revision()
        ):
            raise ValueError("writer returned another generation's bundle")
        if prepared.board:
            bundle = store_board(prepared, complete_board(prepared, output), revision)
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
    principal, latest, latest_revision = await snapshot(
        session, owner, walk_id, request.target_scene_count, request.bundle_format
    )
    current = await repo.current(session, walk_id)
    if latest.board:
        settle_expired(latest, current)
    try:
        require_current(
            ticket, principal, latest.input.source, current.generation if current else 0
        )
    except StaleDiaryGeneration:
        pass
    else:
        complete(current, generation, revision, latest_revision, bundle, failure, datetime.now(UTC))
    value = result(latest, current, latest_revision)
    await session.commit()
    return value
