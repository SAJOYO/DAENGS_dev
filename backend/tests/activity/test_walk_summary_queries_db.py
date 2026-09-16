"""Summary read cost and source validation against disposable PostgreSQL."""

import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, event, select, update

from daengs_backend.models import Walk, WalkAnalysis, WalkPet
from daengs_backend.models.activity import ActivityWalkHead
from daengs_backend.models.walk import WalkCapsule
from daengs_backend.repositories import activity as repo
from daengs_backend.services import activity
from tests.activity.test_activity_db import upload


@contextmanager
def selects(database):
    statements = []
    engine = database.kw["bind"].sync_engine

    def executed(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", executed)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", executed)


async def ready_walk(database, owner, pets, *, observed=True, started=None):
    result = await upload(
        database,
        owner,
        pets,
        uuid.uuid4(),
        started or datetime.now(UTC) - timedelta(minutes=2),
        observed=observed,
    )
    async with database() as db:
        await activity.process_pending(db)
    return result


async def read(database, owner, end, pet=None):
    async with database() as db:
        return await activity.walk_summary(db, owner, 0, end, pet)


async def test_more_walks_do_not_add_per_walk_queries(database, actors, clock):
    (owner, _), (pet, _, _) = actors
    await ready_walk(database, owner, [pet])
    with selects(database) as first_queries:
        first = await read(database, owner, clock[0])
    for _ in range(7):
        await ready_walk(database, owner, [pet])
    with selects(database) as many_queries:
        many = await read(database, owner, clock[0])
    assert many["recorded_walk_count"] == 8
    assert many["moving_distance_m"] == first["moving_distance_m"] * 8
    assert len(many_queries) == len(first_queries)
    assert len(many_queries) <= 5


async def test_processed_summary_does_not_decode_original_payloads(
    database, actors, clock, monkeypatch
):
    (owner, _), (pet, _, _) = actors
    await ready_walk(database, owner, [pet])

    def unexpected_decode(*args):
        raise AssertionError("summary decoded full analysis payload")

    monkeypatch.setattr(activity, "decode_analysis_model", unexpected_decode)
    result = await read(database, owner, clock[0])
    assert result["observed_walk_count"] == 1 and result["moving_distance_m"] > 0


@pytest.mark.parametrize(
    "change",
    [
        "legacy",
        "fingerprint",
        "generation",
        "statistics",
        "versions",
        "metrics",
        "missing_metrics",
        "exclusion",
    ],
)
async def test_incompatible_cache_falls_back_without_rewriting_it(
    database, actors, clock, monkeypatch, change
):
    (owner, _), (pet, _, _) = actors
    walk, _, _ = await ready_walk(database, owner, [pet])
    expected = await read(database, owner, clock[0])
    async with database() as db:
        head = await db.get(ActivityWalkHead, walk.id)
        cached = deepcopy(head.contribution)
        if change == "legacy":
            cached.pop("source_fingerprint")
        elif change == "fingerprint":
            cached["source_fingerprint"] = "stale"
        elif change == "generation":
            cached["generation_id"] = "previous-generation"
        elif change == "statistics":
            cached["statistics_version"] = "previous-statistics"
        elif change == "versions":
            cached["versions"]["facts"] += 1
        elif change == "metrics":
            cached["metrics"]["moving_distance_m"] += (
                123  # Valid integers still need source checks.
            )
        elif change == "missing_metrics":
            cached["metrics"] = None
        else:
            cached["exclusion_reason"] = "non_device_evidence"
        head.contribution = cached
        await db.commit()
    decoded = []
    original = activity.decode_analysis_model

    def decode(row):
        decoded.append(row.id)
        return original(row)

    monkeypatch.setattr(activity, "decode_analysis_model", decode)
    assert await read(database, owner, clock[0]) == expected
    assert len(decoded) == 1
    async with database() as db:
        assert (await db.get(ActivityWalkHead, walk.id)).contribution == cached


@pytest.mark.parametrize(
    "field",
    [
        "moving_s",
        "facts",
        "measurement_receipt",
        "motion_events",
        "micro_observations",
        "observation_version",
    ],
)
async def test_source_corruption_cannot_hide_behind_a_previously_valid_cache(
    database, actors, clock, field
):
    (owner, _), (pet, _, _) = actors
    _, analysis, _ = await ready_walk(database, owner, [pet])
    async with database() as db:
        if field == "facts":
            value = deepcopy(analysis.facts) | {"moving_s": analysis.moving_s + 1}
        elif field == "measurement_receipt":
            value = deepcopy(analysis.measurement_receipt)
            value["received_fix_count"] += 1
        elif field in {"motion_events", "micro_observations"}:
            value = [{"invalid": "payload"}]
        else:
            value = getattr(analysis, field) + 1
        await db.execute(
            update(WalkAnalysis).where(WalkAnalysis.id == analysis.id).values({field: value})
        )
        await db.commit()
    with pytest.raises(ValueError):
        await read(database, owner, clock[0])


@pytest.mark.parametrize("change", ["capsule", "state", "analysis_walk"])
async def test_current_source_and_seal_are_required_even_with_cached_measurements(
    database, actors, clock, change
):
    (owner, _), (pet, _, _) = actors
    walk, analysis, _ = await ready_walk(database, owner, [pet])
    async with database() as db:
        if change == "capsule":
            await db.execute(delete(WalkCapsule).where(WalkCapsule.analysis_id == analysis.id))
        elif change == "state":
            await db.execute(
                update(Walk).where(Walk.id == walk.id).values(analysis_state="collecting")
            )
        else:
            other, _, _ = await ready_walk(database, owner, [pet])
            await db.execute(
                update(WalkAnalysis).where(WalkAnalysis.id == analysis.id).values(walk_id=other.id)
            )
        await db.commit()
    with pytest.raises(ValueError, match="analysis_walk_mismatch|analysis_not_sealed"):
        await read(database, owner, clock[0])


@pytest.mark.parametrize(
    "change", ["revision", "contribution", "processed_analysis", "different_analysis"]
)
async def test_same_session_reads_pending_head_from_current_database(
    database, actors, clock, change
):
    (owner, stranger), (pet, _, other_pet) = actors
    walk, _, _ = await ready_walk(database, owner, [pet])
    other_analysis_id = None
    if change == "different_analysis":
        _, other, _ = await ready_walk(database, stranger, [other_pet])
        other_analysis_id = other.id
    async with database() as db:
        head = await db.get(ActivityWalkHead, walk.id)
        assert (await activity.walk_summary(db, owner, 0, clock[0]))["status"] == "READY"
        await db.commit()
        async with database() as changing:
            values = (
                {"revision": head.revision + 1}
                if change == "revision"
                else {"contribution": None}
                if change == "contribution"
                else {"processed_analysis_id": other_analysis_id}
            )
            await changing.execute(
                update(ActivityWalkHead).where(ActivityWalkHead.walk_id == walk.id).values(**values)
            )
            await changing.commit()
        result = await activity.walk_summary(db, owner, 0, clock[0])
        assert result["status"] == "PENDING" and result["pending_walk_count"] == 1
        assert result["recorded_walk_count"] == 0 and result["moving_distance_m"] is None


async def test_legacy_fallback_is_batched_and_rebuild_restores_cache(
    database, actors, clock, monkeypatch
):
    (owner, _), (pet, _, _) = actors
    for _ in range(8):
        await ready_walk(database, owner, [pet])
    expected = await read(database, owner, clock[0])
    async with database() as db:
        head = await db.scalar(select(ActivityWalkHead).limit(1))
        head.contribution = {
            k: v for k, v in head.contribution.items() if k != "source_fingerprint"
        }
        await db.commit()
    assert await read(database, owner, clock[0]) == expected  # Mixed cache/fallback order.
    async with database() as db:
        for head in await db.scalars(select(ActivityWalkHead)):
            head.contribution = {
                k: v for k, v in head.contribution.items() if k != "source_fingerprint"
            }
        await db.commit()
    with selects(database) as queries:
        assert await read(database, owner, clock[0]) == expected
    assert len(queries) <= 7
    # Also cross batch boundaries without creating hundreds of irrelevant walks.
    monkeypatch.setattr(repo, "READ_BATCH_SIZE", 3)
    assert await read(database, owner, clock[0]) == expected
    async with database() as db:
        await activity.rebuild(db)
    assert (await read(database, owner, clock[0]))["pending_walk_count"] == 8
    async with database() as db:
        assert await activity.process_pending(db) == 8
    assert await read(database, owner, clock[0]) == expected


async def test_valid_source_edit_forces_decode_and_changes_summary(
    database, actors, clock, monkeypatch
):
    (owner, _), (pet, _, _) = actors
    _, analysis, _ = await ready_walk(database, owner, [pet])
    expected = await read(database, owner, clock[0])
    async with database() as db:
        facts = deepcopy(analysis.facts)
        facts["moving_distance_m"] += 10
        facts["distance_m"] += 10
        await db.execute(
            update(WalkAnalysis)
            .where(WalkAnalysis.id == analysis.id)
            .values(facts=facts, moving_distance_m=facts["moving_distance_m"])
        )
        await db.commit()
    decoded = []
    original = activity.decode_analysis_model

    def decode(row):
        decoded.append(row.id)
        return original(row)

    monkeypatch.setattr(activity, "decode_analysis_model", decode)
    result = await read(database, owner, clock[0])
    assert result["moving_distance_m"] == expected["moving_distance_m"] + 10
    assert decoded == [analysis.id]


async def test_current_owner_pet_window_and_unknown_measurements_are_preserved(
    database, actors, clock
):
    (owner, stranger), (pet, pet2, other_pet) = actors
    at = datetime.now(UTC) - timedelta(minutes=5)
    walk, _, _ = await ready_walk(database, owner, [pet, pet2], started=at)
    await ready_walk(database, owner, [pet], started=at, observed=False)
    await ready_walk(database, stranger, [other_pet], started=at)
    end = int((at + timedelta(seconds=60)).timestamp() * 1000)
    assert (await read(database, owner, end))["recorded_walk_count"] == 0  # Exclusive end.
    result = await read(database, owner, end + 1)
    assert result["recorded_walk_count"] == 2 and result["observed_walk_count"] == 1
    assert result["exclusions"] == (("non_device_evidence", 1),)
    assert (await read(database, owner, end + 1, pet2))["recorded_walk_count"] == 1
    with pytest.raises(activity.ActivityNotFound):
        await read(database, stranger, end + 1, pet)
    async with database() as db:
        await db.execute(delete(WalkPet).where(WalkPet.walk_id == walk.id, WalkPet.pet_id == pet2))
        await db.commit()
    assert (await read(database, owner, end + 1, pet2))["recorded_walk_count"] == 0
    async with database() as db:
        await db.execute(delete(Walk).where(Walk.id == walk.id))
        await db.commit()
    remaining = await read(database, owner, end + 1)
    assert remaining["recorded_walk_count"] == 1 and remaining["moving_distance_m"] is None


async def test_changed_capsule_version_rechecks_exclusion(database, actors, clock):
    (owner, _), (pet, _, _) = actors
    _, analysis, _ = await ready_walk(database, owner, [pet])
    async with database() as db:
        await db.execute(
            update(WalkCapsule)
            .where(WalkCapsule.analysis_id == analysis.id)
            .values(capsule_version=analysis.capsule.capsule_version + 1)
        )
        await db.commit()
    result = await read(database, owner, clock[0])
    assert result["recorded_walk_count"] == 1 and result["observed_walk_count"] == 0
    assert result["exclusions"] == (("unsupported_analysis_versions", 1),)
    assert result["moving_distance_m"] is None
