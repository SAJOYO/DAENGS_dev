"""Prepare saved diary input and read responses, including legacy receipt upgrades."""

from dataclasses import replace

from daengs_backend.config import settings
from daengs_backend.orchestration.contracts import PrincipalContext
from daengs_backend.schemas.walk_storyboard import DiaryStoryboardResponse
from daengs_backend.services.walk_diary.legacy.bundle import writing_version
from daengs_backend.services.walk_diary.preparation.board import (
    prepare_saved_base_board,
    with_scene_backgrounds,
)
from daengs_backend.services.walk_diary.preparation.diary import (
    PreparedWalkDiary,
    prepare_saved_diary,
)
from daengs_backend.services.walk_diary.storage.board import load_board, read_board
from daengs_backend.services.walk_diary.storage.bundle import read_diary
from daengs_backend.services.walk_diary.writing.policy import (
    writing_version as slot_writing_version,
)
from daengs_backend.services.walk_storyboard_state import (
    StoryboardConflict,
    StoryboardNotFound,
)
from daengs_walk.diary.board.models import BaseBoardPolicy
from daengs_walk.diary.board.output import BOARD_FORMAT, BOARD_RESPONSE
from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.selection.stamps import StampPolicy


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
    return principal, prepared, generation_revision(prepared, bundle_format)


def generation_revision(prepared, bundle_format):
    revision_parts = {
        "format": bundle_format,
        "plan": prepared.board.plan.revision()
        if prepared.board
        else prepared.prepared.plan.revision(),
        "writer": slot_writing_version() if prepared.board else writing_version(),
    }
    if prepared.board:
        # Provider snapshots are acquired after reservation and frozen in the receipt.
        # Generation identity follows saved input/selection/policy, not future lookup values.
        revision_parts["slots"] = prepared.board.slots.policy.model_dump(mode="json")
        revision_parts["companions"] = prepared.input.pet_names
    return digest(revision_parts)


def apply_backgrounds(prepared, collected):
    return replace(prepared, board=with_scene_backgrounds(prepared.board, collected))


def restore_backgrounds(prepared, row):
    if prepared.board and row is not None and row.status == "ready":
        try:
            collected = getattr(load_board(row.bundle), "scene_backgrounds", None)
            if collected is not None:
                return apply_backgrounds(prepared, collected)
        except ValueError:
            pass  # The normal read path validates damaged or changed source snapshots.
    return prepared


def revisions(source):
    return {r.ref.id: int(r.ref.version) for r in source.records if r.ref.store == "walk_entry"}


def result(prepared, row, revision):
    """Read the published response; legacy receipt upgrades require a caller commit."""
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
