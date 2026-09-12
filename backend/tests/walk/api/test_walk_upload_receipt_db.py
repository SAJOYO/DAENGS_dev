"""Opt-in upload receipts: actual SQL, HTTP, replay, races and route loading cost."""

import asyncio
import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from daengs_backend.config import settings
from daengs_backend.models import Walk, WalkAnalysis, WalkPet, WalkPointChunk
from daengs_backend.models.activity import ActivitySessionLink
from daengs_backend.repositories import walk as walks
from daengs_backend.repositories import walk_upload as repo
from daengs_backend.routers import walk as router
from daengs_backend.schemas.walk import WalkPointsAppend, WalkRecordingRepair
from daengs_backend.services import walk as legacy
from daengs_backend.services import walk_upload_receipt as service
from daengs_backend.services.walk_chunk import encode_chunk
from daengs_backend.services.walk_finalize import FinalizeInputError
from daengs_backend.services.walk_recording import recording_receipt, repair_recording
from tests.walk.api.test_walk_finalize_db import manifest, pending_finalize
from tests.walk.api.test_walk_upload_db import body, http_client
from tests.walk.support.paths import REPO

MODE = "?response=receipt-v1"


def points(request, start, count=2):
    seed = min(request.points, key=lambda p: p.client_seq)
    return [
        seed.model_copy(
            update={
                "client_seq": seq,
                "at": request.started_at + timedelta(milliseconds=seq * 10),
            }
        )
        for seq in range(start, start + count)
    ]


async def upload(database, owner, pet, *, empty=False):
    request = body(uuid.uuid4(), [pet])
    if empty:
        request.points = []
    async with database() as db:
        receipt, created = await service.upload_walk(db, owner, request)
        assert created and not db.in_transaction()
    return receipt.walk_id, request


async def append(database, owner, walk_id, rows):
    async with database() as db:
        receipt = await service.append_points(db, owner, walk_id, WalkPointsAppend(points=rows))
        assert not db.in_transaction()
        return receipt


async def test_http_receipt_replay_and_legacy_detail_are_independent(database, actors):
    (owner, _), (pet, _, foreign) = actors
    request = body(uuid.uuid4(), [pet, foreign, pet])
    async with http_client(database, owner) as http:
        created = await http.post("/app/walks" + MODE, json=request.model_dump(mode="json"))
        assert created.status_code == 201, created.text
        receipt = created.json()
        assert set(receipt) == {"contract_version", "walk_id", "client_session_id", "chunk"}
        assert receipt["contract_version"] == "walk-upload-receipt-v1"
        assert receipt["client_session_id"] == str(request.client_session_id)
        assert receipt["chunk"] == {
            "seq_from": 0,
            "seq_to": 1,
            "point_count": 2,
            "status": "stored",
        }
        request.points.reverse()
        repeated = await http.post("/app/walks" + MODE, json=request.model_dump(mode="json"))
        assert repeated.status_code == 200
        assert repeated.json() == {**receipt, "chunk": {**receipt["chunk"], "status": "replayed"}}
        extra = WalkPointsAppend(points=points(request, 2))
        url = f"/app/walks/{receipt['walk_id']}/points"
        ack = await http.post(url + MODE, json=extra.model_dump(mode="json"))
        assert ack.status_code == 200 and ack.json()["chunk"]["status"] == "stored"
        old = await http.post(url, json=extra.model_dump(mode="json"))
        detail = await http.get(f"/app/walks/{receipt['walk_id']}")
        assert old.status_code == detail.status_code == 200
        assert old.json() == detail.json()
        assert len(detail.json()["points"]) == 4
        assert detail.json()["pet_ids"] == [str(pet)]
        assert detail.json()["recording_receipt"]["known_point_count"] == 4
        old_create = await http.post("/app/walks", json=request.model_dump(mode="json"))
        assert old_create.status_code == 200 and old_create.json() == detail.json()


async def test_empty_create_does_not_ack_missing_points_or_change_metadata(database, actors):
    (owner, _), (pet, _, _) = actors
    walk_id, empty = await upload(database, owner, pet, empty=True)
    request = body(empty.client_session_id, [pet])
    async with http_client(database, owner) as http:
        result = await http.post("/app/walks" + MODE, json=empty.model_dump(mode="json"))
        assert result.status_code == 200 and result.json()["chunk"] is None
        result = await http.post("/app/walks" + MODE, json=request.model_dump(mode="json"))
        assert result.status_code == 409 and result.json()["detail"]["code"] == "walk_chunk_missing"
        for field, value in (
            ("weather_code", 62),
            ("ended_at", empty.ended_at + timedelta(seconds=1)),
        ):
            altered = empty.model_copy(update={field: value})
            result = await http.post("/app/walks" + MODE, json=altered.model_dump(mode="json"))
            assert result.status_code == 409
            assert result.json()["detail"]["code"] == "walk_upload_conflict"
    await append(database, owner, walk_id, request.points)
    async with database() as db:
        ack, created = await service.upload_walk(db, owner, request)
        assert not created and ack.chunk.status == "replayed"


@pytest.mark.parametrize(
    "change", ["coordinate", "time", "accuracy", "eligibility", "length", "overlap", "gap"]
)
async def test_conflicting_chunks_never_receive_success_or_change_data(database, actors, change):
    (owner, _), (pet, _, _) = actors
    walk_id, request = await upload(database, owner, pet)
    rows = sorted(request.model_copy(deep=True).points, key=lambda p: p.client_seq)
    if change == "coordinate":
        rows[0].lng += 1
    elif change == "time":
        rows[0].at += timedelta(seconds=1)
    elif change == "accuracy":
        rows[0].accuracy_m = 99
    elif change == "eligibility":
        rows[0].recording_eligible = False
    elif change == "length":
        rows = rows[:1]
    elif change == "overlap":
        rows = points(request, 1)
    else:
        rows = [*points(request, 2, 1), *points(request, 4, 1)]
    async with http_client(database, owner) as http:
        original = (await http.get(f"/app/walks/{walk_id}")).json()
        result = await http.post(
            f"/app/walks/{walk_id}/points" + MODE,
            json=WalkPointsAppend(points=rows).model_dump(mode="json"),
        )
        code = {"overlap": "walk_chunk_overlap", "gap": "walk_chunk_not_contiguous"}.get(
            change, "walk_chunk_conflict"
        )
        assert result.status_code == 409 and result.json()["detail"]["code"] == code
        assert (await http.get(f"/app/walks/{walk_id}")).json() == original


async def test_canonical_replay_and_recording_repair_remain_compatible(database, actors):
    (owner, _), (pet, _, _) = actors
    request = body(uuid.uuid4(), [pet])
    for p in request.points:
        p.recording_eligible = None
    async with database() as db:
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        for filename in (
            "db/init/19_walk_entries.sql",
            "db/init/24_walk_entry_contexts.sql",
            "db/init/32_walk_context_recollection.sql",
            "db/migrations/2026-09-09_walk_entry_pins.sql",
            "db/init/25_walk_entry_pins.sql",
        ):
            await raw.execute((REPO / filename).read_text("utf-8"))
        await db.commit()
        ack, _ = await service.upload_walk(db, owner, request)
        missing = recording_receipt(request.points)
        enriched = request.model_copy(deep=True)
        for p in enriched.points:
            p.recording_eligible = True
        with pytest.raises(legacy.WalkStateConflictError, match="내용이 다릅니다"):
            await service.append_points(
                db, owner, ack.walk_id, WalkPointsAppend(points=enriched.points)
            )
        await repair_recording(
            db,
            owner,
            ack.walk_id,
            WalkRecordingRepair(
                contract_version="gps-recording-v1",
                policy_version="gps-recording-eligibility-v1",
                raw_input_fingerprint=missing.raw_input_fingerprint,
                points=enriched.points,
            ),
        )
        # Unknown eligibility is not a request to erase the repaired observation.
        replay, _ = await service.upload_walk(db, owner, request)
        assert replay.chunk.status == "replayed"
        for p in enriched.points:
            p.at += timedelta(microseconds=100)
            p.lat += type(p.lat)("0.0000001")
        replay = await service.append_points(
            db, owner, ack.walk_id, WalkPointsAppend(points=enriched.points)
        )
        assert replay.chunk.status == "replayed"


async def test_auth_unknown_mode_and_wrong_owner(database, actors):
    (owner, other), (pet, _, _) = actors
    walk_id, request = await upload(database, owner, pet)
    payload = WalkPointsAppend(points=request.points).model_dump(mode="json")
    async with http_client(database, other) as http:
        for identifier in (walk_id, uuid.uuid4()):
            result = await http.post(f"/app/walks/{identifier}/points" + MODE, json=payload)
            assert result.status_code == 404
        invalid = await http.post(
            "/app/walks?response=receipt-v2", json=request.model_dump(mode="json")
        )
        assert invalid.status_code == 422
        # The same client id is scoped to the owner, even in receipt mode.
        own = await http.post("/app/walks" + MODE, json=request.model_dump(mode="json"))
        assert own.status_code == 201 and own.json()["walk_id"] != str(walk_id)
    app = FastAPI()
    app.include_router(router.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        result = await http.post("/app/walks" + MODE, json=request.model_dump(mode="json"))
        assert result.status_code == 401


@pytest.mark.parametrize("game_enabled", [False, True])
@pytest.mark.parametrize("different", [False, True])
async def test_concurrent_creation_converges_and_preserves_first_content(
    database, actors, monkeypatch, game_enabled, different
):
    (owner, _), (pet, _, _) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", game_enabled)
    first = body(uuid.uuid4(), [pet])
    second = first.model_copy(deep=True)
    if different:
        second.points[0].lng += 1
    lookup = repo.by_client_session
    barrier = asyncio.Barrier(2)

    async def racing_lookup(*args):
        row = await lookup(*args)
        if row is None and not game_enabled:
            await asyncio.wait_for(barrier.wait(), timeout=5)
        return row

    monkeypatch.setattr(repo, "by_client_session", racing_lookup)
    async with http_client(database, owner) as http:
        responses = await asyncio.wait_for(
            asyncio.gather(
                *[
                    http.post("/app/walks" + MODE, json=value.model_dump(mode="json"))
                    for value in (first, second)
                ]
            ),
            timeout=10,
        )
        assert sorted(r.status_code for r in responses) == ([201, 409] if different else [200, 201])
        winner = next(r.json() for r in responses if r.status_code == 201)
        replay = await http.post(
            "/app/walks" + MODE,
            json=(first if responses[0].status_code == 201 else second).model_dump(mode="json"),
        )
        assert replay.status_code == 200 and replay.json()["walk_id"] == winner["walk_id"]
    async with database() as db:
        for table in (Walk, WalkPet, WalkPointChunk):
            assert await db.scalar(select(func.count()).select_from(table)) == 1
        assert await db.scalar(select(func.count()).select_from(ActivitySessionLink)) == int(
            game_enabled
        )


@pytest.mark.parametrize("different", [False, True])
async def test_concurrent_append_and_stale_identity_map(database, actors, different):
    (owner, _), (pet, _, _) = actors
    walk_id, request = await upload(database, owner, pet)
    rows = points(request, 2)
    changed = [p.model_copy(deep=True) for p in rows]
    if different:
        changed[0].lng += 1
    async with database() as stale:
        await walks.get_owned(stale, owner, walk_id)
        await stale.commit()
        results = await asyncio.gather(
            append(database, owner, walk_id, rows),
            append(database, owner, walk_id, changed),
            return_exceptions=True,
        )
        if different:
            assert sum(isinstance(r, legacy.WalkStateConflictError) for r in results) == 1
        else:
            assert {r.chunk.status for r in results} == {"stored", "replayed"}
        current = await repo.chunk(stale, walk_id, 2)
        await stale.commit()
        # Populate-existing must refresh a previously loaded chunk after an external change.
        altered = [p.model_copy(update={"lng": p.lng + 2}) for p in rows]
        async with database() as db:
            await db.execute(
                update(WalkPointChunk)
                .where(WalkPointChunk.walk_id == walk_id, WalkPointChunk.seq_from == 2)
                .values(payload=encode_chunk(altered))
            )
            await db.commit()
        with pytest.raises(legacy.WalkStateConflictError):
            await service.append_points(stale, owner, walk_id, WalkPointsAppend(points=rows))
        assert not stale.in_transaction()
        assert current is not None


@pytest.mark.parametrize("game_enabled", [False, True])
async def test_legacy_and_receipt_initial_uploads_share_one_walk(
    database, actors, monkeypatch, game_enabled
):
    (owner, _), (pet, _, _) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", game_enabled)
    request = body(uuid.uuid4(), [pet])
    barrier = asyncio.Barrier(2)

    def racing(original):
        async def lookup(*args):
            result = await original(*args)
            if result is None and not game_enabled:
                await asyncio.wait_for(barrier.wait(), timeout=5)
            return result

        return lookup

    monkeypatch.setattr(repo, "by_client_session", racing(repo.by_client_session))
    monkeypatch.setattr(walks, "get_by_client_session", racing(walks.get_by_client_session))
    async with http_client(database, owner) as http:
        old, new = await asyncio.wait_for(
            asyncio.gather(
                http.post("/app/walks", json=request.model_dump(mode="json")),
                http.post("/app/walks" + MODE, json=request.model_dump(mode="json")),
            ),
            timeout=10,
        )
        assert sorted((old.status_code, new.status_code)) == [200, 201]
        assert old.json()["id"] == new.json()["walk_id"]
        assert len(old.json()["points"]) == new.json()["chunk"]["point_count"] == 2


@pytest.mark.parametrize("operation", ["create", "append"])
async def test_cancelled_write_has_no_partial_success(database, actors, monkeypatch, operation):
    (owner, _), (pet, _, _) = actors
    walk_id, request = await upload(database, owner, pet)
    async with database() as db:

        async def cancel_commit():
            await db.flush()
            raise asyncio.CancelledError

        with monkeypatch.context() as scoped:
            scoped.setattr(db, "commit", cancel_commit)
            with pytest.raises(asyncio.CancelledError):
                if operation == "create":
                    await service.upload_walk(db, owner, body(uuid.uuid4(), [pet]))
                else:
                    await service.append_points(
                        db, owner, walk_id, WalkPointsAppend(points=points(request, 2))
                    )
        assert not db.in_transaction()
        for table in (Walk, WalkPointChunk):
            assert await db.scalar(select(func.count()).select_from(table)) == 1


@pytest.mark.parametrize("damage", ["count", "overlap", "version"])
async def test_legacy_chunk_damage_cannot_be_acknowledged(database, actors, damage):
    (owner, _), (pet, _, _) = actors
    walk_id, request = await upload(database, owner, pet)
    async with database() as db:
        stored = await repo.chunk(db, walk_id, 0)
        if damage == "count":
            stored.point_count += 1
        elif damage == "version":
            stored.payload = {**stored.payload, "v": 999}
        else:
            # The old API permits overlapping chunks; receipt mode must detect them.
            overlap = WalkPointChunk(
                walk_id=walk_id,
                seq_from=1,
                seq_to=2,
                point_count=2,
                payload=encode_chunk(points(request, 1)),
            )
            db.add(overlap)
        await db.commit()
        with pytest.raises((legacy.WalkStateConflictError, ValueError)):
            await service.append_points(db, owner, walk_id, WalkPointsAppend(points=request.points))
        assert not db.in_transaction()


async def test_receipts_still_require_finalize_and_reuse_the_same_analysis(database, actors):
    (owner, _), (pet, _, _) = actors
    request = body(uuid.uuid4(), [pet])
    tail = request.model_copy(update={"points": points(request, 2)})
    async with database() as db:
        ack, _ = await service.upload_walk(db, owner, tail)
        assert ack.chunk.seq_from == 2
        with pytest.raises(FinalizeInputError):
            await legacy.finalize_walk(db, owner, ack.walk_id, manifest(4), None)
        await db.rollback()
        await service.append_points(db, owner, ack.walk_id, WalkPointsAppend(points=request.points))
        analysis, created = await legacy.finalize_walk(db, owner, ack.walk_id, manifest(4), None)
        analysis_id = analysis.id
        assert created
        again, created = await legacy.finalize_walk(db, owner, ack.walk_id, manifest(4), None)
        assert not created and again.id == analysis_id
        receipt, created = await service.upload_walk(db, owner, tail)
        assert not created and receipt.chunk.status == "replayed"
        with pytest.raises(legacy.WalkStateConflictError) as failure:
            await service.append_points(
                db, owner, ack.walk_id, WalkPointsAppend(points=tail.points)
            )
        assert failure.value.code == "walk_already_finalized"


@pytest.mark.parametrize("game_enabled", [False, True])
async def test_append_during_weather_invalidates_the_old_finalize_input(
    database, actors, monkeypatch, game_enabled
):
    (owner, _), (pet, _, _) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", game_enabled)
    walk_id, request = await upload(database, owner, pet)
    async with pending_finalize(database, owner, walk_id) as (task, weather):
        ack = await asyncio.wait_for(
            append(database, owner, walk_id, points(request, 2)), timeout=3
        )
        assert ack.chunk.status == "stored" and not task.done()
        weather.resume.set()
        with pytest.raises(FinalizeInputError):
            await task
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(WalkAnalysis)) == 0


@pytest.mark.parametrize("operation", ["create", "append"])
async def test_commit_failure_rolls_back_before_a_success_receipt(
    database, actors, monkeypatch, operation
):
    (owner, _), (pet, _, _) = actors
    walk_id, request = await upload(database, owner, pet)
    monkeypatch.setattr(settings, "activity_game_enabled", True)
    async with database() as db:
        original_commit = db.commit

        async def fail_commit():
            await db.flush()
            raise RuntimeError("storage unavailable")

        with monkeypatch.context() as scoped:
            scoped.setattr(db, "commit", fail_commit)
            with pytest.raises(RuntimeError, match="storage unavailable"):
                if operation == "create":
                    await service.upload_walk(db, owner, body(uuid.uuid4(), [pet]))
                else:
                    await service.append_points(
                        db, owner, walk_id, WalkPointsAppend(points=points(request, 2))
                    )
        assert not db.in_transaction()
        assert db.commit == original_commit
        assert await db.scalar(select(func.count()).select_from(Walk)) == 1
        assert await db.scalar(select(func.count()).select_from(WalkPointChunk)) == 1
        assert await db.scalar(select(func.count()).select_from(ActivitySessionLink)) == 0
        await db.rollback()
        if operation == "append":
            ack = await service.append_points(
                db, owner, walk_id, WalkPointsAppend(points=points(request, 2))
            )
            assert ack.chunk.status == "stored"


async def test_other_unique_errors_are_not_treated_as_retries(database, actors, monkeypatch):
    (owner, _), (pet, _, _) = actors
    original_add = walks.add

    def invalid_add(db, walk):
        walk.pets.append(WalkPet(pet_id=pet))
        return original_add(db, walk)

    monkeypatch.setattr(walks, "add", invalid_add)
    async with database() as db:
        with pytest.raises(IntegrityError) as failure:
            await service.upload_walk(db, owner, body(uuid.uuid4(), [pet]))
        assert failure.value.orig.__cause__.constraint_name != "walks_client_session_unique"
        assert not db.in_transaction()
        assert await db.scalar(select(func.count()).select_from(Walk)) == 0


@contextmanager
def loaded_chunks(factory):
    engine = factory.kw["bind"].sync_engine
    rows, queries = [], []

    def loaded(session, value):
        if isinstance(value, WalkPointChunk):
            rows.append(value)

    def query(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(Session, "loaded_as_persistent", loaded)
    event.listen(engine, "before_cursor_execute", query)
    try:
        yield rows, queries
    finally:
        event.remove(Session, "loaded_as_persistent", loaded)
        event.remove(engine, "before_cursor_execute", query)


@pytest.mark.parametrize("chunk_count", [1, 10, 50])
async def test_response_and_payload_loading_do_not_grow_with_previous_chunks(
    database, actors, chunk_count
):
    (owner, _), (pet, _, _) = actors
    request = body(uuid.uuid4(), [pet])
    request.points = points(request, 0, 100)
    async with database() as db:
        ack, _ = await service.upload_walk(db, owner, request)
        for index in range(1, chunk_count):
            rows = points(request, index * 100, 100)
            db.add(
                WalkPointChunk(
                    walk_id=ack.walk_id,
                    seq_from=index * 100,
                    seq_to=index * 100 + 99,
                    point_count=100,
                    payload=encode_chunk(rows),
                )
            )
        await db.commit()
    extra = WalkPointsAppend(points=points(request, chunk_count * 100, 100))
    async with http_client(database, owner) as http:
        url = f"/app/walks/{ack.walk_id}/points"
        with loaded_chunks(database) as (loaded, queries):
            receipt = await http.post(url + MODE, json=extra.model_dump(mode="json"))
        assert receipt.status_code == 200
        assert len(loaded) == 0 and len(queries) == 3
        with loaded_chunks(database) as (loaded, queries):
            replay = await http.post(url + MODE, json=extra.model_dump(mode="json"))
        assert replay.status_code == 200
        assert len(loaded) == 1 and loaded[0].point_count == 100 and len(queries) == 3
        with loaded_chunks(database) as (loaded, queries):
            initial = await http.post("/app/walks" + MODE, json=request.model_dump(mode="json"))
        assert initial.status_code == 200
        assert len(loaded) == 1 and len(queries) == 3
        with loaded_chunks(database) as (loaded, _):
            detail = await http.post(url, json=extra.model_dump(mode="json"))
        assert detail.status_code == 200
        assert len(loaded) == chunk_count + 1
        assert len(receipt.content) < 300 and len(replay.content) < 300
        assert len(detail.json()["points"]) == (chunk_count + 1) * 100
        print(
            f"chunks={chunk_count}: receipt_bytes={len(receipt.content)}, legacy_bytes={len(detail.content)}, new_payload_rows=0, replay_payload_rows=1, legacy_payload_rows={len(loaded)}"
        )
