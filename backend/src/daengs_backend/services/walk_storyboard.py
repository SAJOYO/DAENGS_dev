"""Versioned, retryable scene generation after GPS finalization and entry synchronization."""

import asyncio
from datetime import UTC, datetime, timedelta

from daengs_backend.models.walk_storyboard import WalkStoryboard
from daengs_backend.repositories import walk as walks
from daengs_backend.repositories import walk_entry as entries_repo
from daengs_backend.repositories import walk_storyboard as repo
from daengs_backend.schemas.walk import WalkFinalizeRequest
from daengs_backend.schemas.walk_storyboard import StoryboardResponse
from daengs_backend.services.walk_entry import response as entry_response
from daengs_backend.services.walk_finalize import prepare_finalized_walk
from daengs_backend.services.walk_storyboard_context import unavailable_contexts
from daengs_walk import analyze_walk
from daengs_walk.storyboard import StoryboardBundleV2, build_storyboard, fingerprint, legacy_bundle
from daengs_walk.storyboard_input import scene_inputs
from daengs_walk.storyboard_selection import ReferenceWalk

POLICY_VERSION = "live-storyboard-v2"
LEASE_SECONDS = 60
CONTEXT_TIMEOUT_SECONDS = 10


class StoryboardNotFound(LookupError):
    pass


class StoryboardConflict(ValueError):
    pass


async def source(session, owner, walk_id):
    walk = await walks.get_owned_for_update(session, owner, walk_id)
    if walk is None:
        raise StoryboardNotFound
    analysis = await repo.latest_analysis(session, walk_id)
    if walk.analysis_state != "derived" or analysis is None:
        raise StoryboardConflict("GPS 업로드와 산책 계산을 먼저 완료해 주세요.")
    rows = await entries_repo.entries(session, [walk_id])
    if len(rows) > 200:
        raise StoryboardConflict("이번 장면 계약은 최대 200개 기록을 지원합니다.")
    entries = [entry_response(r).model_dump(mode="json") for r in rows]
    revisions = {r["id"]: r["revision"] for r in entries}
    history = await repo.reference_walks(session, walk)
    revision = fingerprint(
        {
            "policy": POLICY_VERSION,
            "analysis_id": str(analysis.id),
            "input": analysis.input_fingerprint,
            "entries": entries,
            "history": [str(w.id) for w in history],
        }
    )
    return walk, analysis, entries, revisions, revision, history


def result(walk, row, revisions, revision, bundle_format="walk-storyboard-candidates-v1"):
    state = "pending" if row is None else "stale" if row.input_revision != revision else row.status
    bundle = row.bundle if row is not None and state == "ready" else None
    if bundle is not None and bundle_format == "walk-storyboard-candidates-v1":
        bundle = legacy_bundle(StoryboardBundleV2.model_validate(bundle))
    return StoryboardResponse(
        session_id=walk.client_session_id,
        generation=row.generation if row else 0,
        input_revision=revision,
        status=state,
        entry_revisions=revisions,
        bundle=bundle,
        error_code=row.error_code if row is not None and state == "failed" else None,
    )


async def get(session, owner, walk_id, bundle_format="walk-storyboard-candidates-v1"):
    walk, _, _, revisions, revision, _ = await source(session, owner, walk_id)
    value = result(walk, await repo.current(session, walk_id), revisions, revision, bundle_format)
    await session.commit()
    return value


async def generate(session, owner, walk_id, request, lookup):
    walk, analysis, entries, revisions, revision, history = await source(session, owner, walk_id)
    if {str(k): v for k, v in request.expected_entries.items()} != revisions:
        raise StoryboardConflict("행동 기록이 변경됐어요. 기록을 다시 동기화해 주세요.")
    row = await repo.current(session, walk_id)
    now = datetime.now(UTC)
    if row is not None and row.input_revision == revision:
        running = row.status == "running" and row.updated_at > now - timedelta(
            seconds=LEASE_SECONDS
        )
        if running or (row.status == "ready" and not request.refresh):
            value = result(walk, row, revisions, revision, request.bundle_format)
            await session.commit()
            return value
    prepared = prepare_finalized_walk(
        walk.points,
        WalkFinalizeRequest(
            expected_point_count=analysis.point_count,
            terminal_client_seq=analysis.terminal_client_seq,
            input_fingerprint=analysis.input_fingerprint,
        ),
    )
    started_at, ended_at, session_id = walk.started_at, walk.ended_at, str(walk.client_session_id)
    pet_id = str(walk.pet_ids[0]) if len(walk.pet_ids) == 1 else None
    generation = (row.generation if row else 0) + 1
    if row is None:
        row = WalkStoryboard(walk_id=walk_id)
        session.add(row)
    row.generation, row.input_revision = generation, revision
    row.status, row.updated_at, row.error_code = "running", now, None
    row.bundle = None
    await session.commit()  # No row lock or active DB transaction during environment I/O.
    bundle, failure = None, None
    try:
        evidence = await asyncio.to_thread(
            analyze_walk, walk_id, started_at, ended_at, prepared.points
        )
        references = await asyncio.to_thread(history_references, history, pet_id)
        projected, selection = scene_inputs(
            evidence, entries, session_id=session_id, pet_id=pet_id, references=references
        )
        try:
            contexts = await asyncio.wait_for(lookup(selection), timeout=CONTEXT_TIMEOUT_SECONDS)
        except TimeoutError:
            contexts = unavailable_contexts(selection)
        bundle = build_storyboard(
            session_id,
            started_at,
            ended_at,
            evidence.facts.moving_distance_m,
            projected,
            selection,
            contexts,
            evidence.gaps,
        ).model_dump(mode="json")
    except asyncio.CancelledError:
        raise  # Persisted lease makes a killed request recoverable after 60s.
    except Exception:  # noqa: BLE001 - persist a bounded failed state; never expose provider/DB secrets
        failure = "scene_analysis_failed"

    # Entries can change, another request can claim an expired lease, or the owner can delete the walk.
    session.expire_all()
    latest_walk, _, _, latest_revisions, latest_revision, _ = await source(session, owner, walk_id)
    current = await repo.current(session, walk_id)
    if current and current.generation == generation and latest_revision == revision:
        current.status = "failed" if failure else "ready"
        current.bundle, current.error_code, current.updated_at = bundle, failure, datetime.now(UTC)
    value = result(latest_walk, current, latest_revisions, latest_revision, request.bundle_format)
    await session.commit()
    return value


def history_references(history, pet_id):
    references = []
    for walk in history:
        count = sum(c.point_count for c in walk.points)
        prepared = prepare_finalized_walk(
            walk.points,
            WalkFinalizeRequest(
                expected_point_count=count, terminal_client_seq=count - 1 if count else None
            ),
        )
        evidence = analyze_walk(walk.id, walk.started_at, walk.ended_at, prepared.points)
        _, selected = scene_inputs(
            evidence, [], session_id=str(walk.client_session_id), pet_id=pet_id
        )
        speed = selected["movement_summary"]["median_speed_mps"]
        if speed is not None and 0 < speed <= 10:
            references.append(
                ReferenceWalk(
                    walk_id=str(walk.id),
                    pet_id=pet_id,
                    started_at=walk.started_at,
                    median_speed_mps=speed,
                )
            )
    return references
