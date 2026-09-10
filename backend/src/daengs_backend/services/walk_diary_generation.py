"""Opt-in diary branch of the existing WalkStoryboard lifecycle; no second queue/table."""

import asyncio
from datetime import UTC, datetime

from daengs_backend.config import settings
from daengs_backend.orchestration.contracts import PrincipalContext
from daengs_backend.repositories import walk_storyboard as repo
from daengs_backend.schemas.walk_storyboard import DiaryStoryboardResponse
from daengs_backend.services.walk_diary_contract import (
    StaleDiaryGeneration,
    bind_generation,
    require_current,
)
from daengs_backend.services.walk_diary_prepare import prepare_saved_diary
from daengs_backend.services.walk_diary_storage import read_diary, store_diary
from daengs_backend.services.walk_diary_writing import write_diary, writing_version
from daengs_backend.services.walk_storyboard_state import (
    StoryboardConflict,
    StoryboardNotFound,
    complete,
    reserve,
    reusable,
)
from daengs_walk.diary_input import digest
from daengs_walk.diary_stamps import StampPolicy


async def snapshot(session, owner, walk_id, target):
    if not settings.walk_diary_enabled:
        raise StoryboardNotFound
    if target is None:
        raise StoryboardConflict("일기 장면 목표 수를 지정해 주세요.")
    principal = PrincipalContext(kind="APP_USER", subject=str(owner))
    try:
        prepared = await prepare_saved_diary(
            session, principal, walk_id, StampPolicy(target_scene_count=target)
        )
    except LookupError:
        raise StoryboardNotFound from None
    except ValueError:
        raise StoryboardConflict(
            "일기 원본을 준비할 수 없습니다. 기록 동기화를 확인해 주세요."
        ) from None
    revision = digest(
        {
            "format": "walk-diary-bundle-v1",
            "plan": prepared.prepared.plan.revision(),
            "writer": writing_version(),
        }
    )
    return principal, prepared, revision


def revisions(source):
    return {r.ref.id: int(r.ref.version) for r in source.records if r.ref.store == "walk_entry"}


def result(prepared, row, revision):
    source, plan = prepared.input.source, prepared.prepared.plan
    state = "pending" if row is None else "stale" if row.input_revision != revision else row.status
    bundle, error = None, row.error_code if row is not None and state == "failed" else None
    counts, limits = prepared.prepared.counts, prepared.prepared.limits
    background_update = False
    if row is not None and row.status == "ready":
        try:
            stored = read_diary(prepared, row, revision)
            if stored is not None:
                state, bundle = "ready", stored.bundle
                counts, limits = stored.preparation_counts, stored.preparation_limits
                background_update = row.input_revision != revision
            else:
                state = "stale"
        except ValueError:
            state, error, bundle = "failed", "invalid_stored_diary", None
    return DiaryStoryboardResponse(
        format="walk-diary-response-v1",
        session_id=source.client_session_id,
        generation=row.generation if row else 0,
        input_revision=row.input_revision if bundle is not None else revision,
        status=state,
        entry_revisions=revisions(source),
        photos_status=source.photos_status,
        photo_manifest=source.photo_manifest,
        target_scene_count=plan.target_scene_count,
        preparation_counts=counts,
        preparation_limits=limits,
        background_update_available=background_update,
        bundle=bundle,
        error_code=error,
    )


async def get_diary(session, owner, walk_id, target):
    _, prepared, revision = await snapshot(session, owner, walk_id, target)
    value = result(prepared, await repo.current(session, walk_id), revision)
    await session.commit()
    return value


async def generate_diary(session, owner, walk_id, request, *, writer=None):
    principal, prepared, revision = await snapshot(
        session, owner, walk_id, request.target_scene_count
    )
    source = prepared.input.source
    if {str(k): v for k, v in request.expected_entries.items()} != revisions(source):
        raise StoryboardConflict("행동 기록이 변경됐어요. 기록을 다시 동기화해 주세요.")
    if request.expected_photo_manifest != source.photo_manifest:
        raise StoryboardConflict("사진 목록이 변경됐어요. 승인된 사진 버전을 확인해 주세요.")
    row = await repo.current(session, walk_id)
    now = datetime.now(UTC)
    value = result(prepared, row, revision)
    if (value.status == "ready" and not request.refresh) or (
        row is not None
        and row.status == "running"
        and reusable(row, revision, request.refresh, now)
    ):
        await session.commit()
        return value
    generation = reserve(session, walk_id, row, revision, now)
    ticket = bind_generation(principal, source, generation)
    await session.commit()  # Release the Walk lock/transaction before the LLM call.
    bundle, failure = None, None
    try:
        output = await (writer or write_diary)(source, prepared.prepared)
        if (
            output.input_revision != ticket.input_revision
            or output.plan_revision != prepared.prepared.plan.revision()
        ):
            raise ValueError("writer returned another generation's bundle")
        bundle = store_diary(prepared, output, revision)
    except asyncio.CancelledError:
        raise  # The shared 60-second lease permits recovery after interruption.
    except Exception:  # noqa: BLE001 - never persist raw source/provider exception details
        bundle, failure = None, "diary_generation_failed"
    principal, latest, latest_revision = await snapshot(
        session, owner, walk_id, request.target_scene_count
    )
    current = await repo.current(session, walk_id)
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
