"""Opt-in HTTP lifecycle: committed reservation -> relational writer -> CAS JSONB publication."""

import asyncio
from datetime import UTC, datetime, timedelta

from daengs_backend.config import settings
from daengs_backend.repositories import walk_storyboard as repo
from daengs_backend.schemas.walk_relational_diary import (
    RELATIONAL_FORMAT,
    RELATIONAL_PENDING,
    RELATIONAL_STORAGE,
    RelationalDiaryResponse,
)
from daengs_backend.services.walk_diary.preparation.input import read_input
from daengs_backend.services.walk_diary.preparation.relational_base import assemble_relational_base
from daengs_backend.services.walk_diary.relational_execution import (
    RelationalConfigurationError,
    RelationalExecutionPolicy,
)
from daengs_backend.services.walk_diary.storage.relational_db import (
    read_result,
    source_revision,
    store_result,
)
from daengs_backend.services.walk_generation.state import (
    LEASE_SECONDS,
    StoryboardConflict,
    StoryboardNotFound,
    complete,
    reserve,
)


def is_relational(row):
    return (
        row is not None
        and isinstance(row.bundle, dict)
        and row.bundle.get("format")
        in {
            RELATIONAL_STORAGE,
            RELATIONAL_PENDING,
        }
    )


def active(row, now):
    if row is None or row.status != "running":
        return False
    raw = row.bundle or {}
    if raw.get("format") in {RELATIONAL_PENDING, "walk-diary-preparation-v1"}:
        try:
            return now < datetime.fromisoformat(raw["deadline_at"])
        except (ValueError, KeyError, TypeError):
            return False
    return row.updated_at > now - timedelta(seconds=LEASE_SECONDS)


async def source(session, owner, walk_id):
    if not settings.walk_diary_enabled:
        raise StoryboardNotFound
    try:
        assembled = await read_input(session, owner, walk_id)
    except LookupError:
        raise StoryboardNotFound from None
    except ValueError:
        raise StoryboardConflict("산책 원본 동기화를 확인해 주세요.") from None
    return assembled


def response(assembled, row, target, now):
    current = assembled.source
    revision = source_revision(assembled)
    status, bundle, error = "pending", None, None
    limits = {}
    if is_relational(row):
        status = "stale" if revision != row.input_revision else row.status
        if status == "running" and not active(row, now):
            status, error = "failed", "generation_deadline_exceeded"
        elif status == "failed":
            error = row.error_code
        if status == "ready":
            try:
                bundle = read_result(
                    row.bundle,
                    walk_id=current.walk_id,
                    session_id=current.client_session_id,
                    revision=revision,
                    generation=row.generation,
                )
                target = row.bundle["payload"]["target"]
            except (ValueError, KeyError, TypeError):
                status, error = "failed", "invalid_stored_relational_diary"
        elif row.bundle.get("format") == RELATIONAL_PENDING:
            limits = row.bundle.get("execution_limits", {})
    return RelationalDiaryResponse(
        session_id=current.client_session_id,
        generation=row.generation if row else 0,
        input_revision=revision,
        status=status,
        bundle=bundle,
        error_code=error,
        target_scene_count=target,
        execution_limits=limits,
        entry_revisions={
            r.ref.id: int(r.ref.version) for r in current.records if r.ref.store == "walk_entry"
        },
        photos_status=current.photos_status,
        photo_manifest=current.photo_manifest,
    )


async def get_relational(session, owner, walk_id, target=None):
    assembled = await source(session, owner, walk_id)
    row = await repo.current(session, walk_id)
    value = response(assembled, row, target or 3, datetime.now(UTC))
    await session.commit()
    return value


async def generate_relational(session, owner, walk_id, request, *, writer=None, policy=None):
    from daengs_backend.services.walk_diary.runtime import write_relational_board

    policy = policy or RelationalExecutionPolicy()
    assembled = await source(session, owner, walk_id)
    row = await repo.current(session, walk_id)
    now = datetime.now(UTC)
    target = request.target_scene_count
    value = response(assembled, row, target, now)
    if (
        {str(k): v for k, v in request.expected_entries.items()} != value.entry_revisions
        or request.expected_photo_manifest != assembled.source.photo_manifest
    ):
        raise StoryboardConflict("행동 기록이나 사진 목록이 변경됐어요. 다시 동기화해 주세요.")
    if active(row, now):
        if not is_relational(row):
            raise StoryboardConflict("기존 일기 생성이 진행 중입니다.")
        # Source edits may supersede the old generation, but a repeated POST cannot.
        if row.input_revision == value.input_revision:
            await session.commit()
            return value
    if value.status == "ready" and not request.refresh:
        await session.commit()
        return value
    base = assemble_relational_base(assembled, target)
    revision = value.input_revision
    # Writing includes pacing/reviews. Publication gets a short separate completion margin.
    execution_s = policy.preparation_timeout_s + policy.generation_timeout_s
    deadline = now + timedelta(seconds=execution_s + 15)
    pending = {
        "format": RELATIONAL_PENDING,
        "bundle_format": RELATIONAL_FORMAT,
        "deadline_at": deadline.isoformat(),
        "target": target,
        "execution_limits": {
            "generation_seconds": policy.generation_timeout_s,
            "minimum_call_interval_seconds": policy.minimum_interval_s,
        },
    }
    generation = reserve(session, walk_id, row, revision, now, pending_bundle=pending)
    await session.commit()  # No source/Walk lock survives provider or LLM I/O.
    bundle, failure = None, None
    try:
        async with asyncio.timeout(execution_s):
            output = await (writer or write_relational_board)(
                assembled.source,
                base,
                execution_policy=policy,
            )
        bundle = store_result(output, base, revision=revision, generation=generation, target=target)
    except asyncio.CancelledError:
        raise  # Frozen pending deadline remains; GET never publishes replacement prose.
    except TimeoutError:
        failure = "relational_generation_timeout"
    except RelationalConfigurationError:
        failure = "relational_model_not_configured"
    except Exception:  # noqa: BLE001 -- never persist provider secrets or raw exceptions
        failure = "relational_generation_failed"
    latest = await source(session, owner, walk_id)  # Fresh identity map and same Walk lock.
    row = await repo.current(session, walk_id)
    now = datetime.now(UTC)
    if is_relational(row) and now < deadline:
        complete(row, generation, revision, source_revision(latest), bundle, failure, now)
        # Retain format recognition for failed results too.
        if row.generation == generation and row.status == "failed":
            row.bundle = pending
    value = response(latest, row, target, now)
    await session.commit()
    return value
