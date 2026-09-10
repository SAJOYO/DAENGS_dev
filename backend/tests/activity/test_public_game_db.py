"""Public rankings and ownership using disposable PostgreSQL, including exact SQL arithmetic."""

from dataclasses import asdict
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from daengs_backend.models.activity import ActivityAccount, ActivitySeason
from daengs_backend.models.app_user import AppUser
from daengs_backend.models.pet import Pet
from daengs_backend.models.pet_member import PetMember
from daengs_backend.models.territory_claim import TerritoryOccupancy
from daengs_backend.repositories import territory_game as repo
from daengs_backend.services import activity, activity_game
from daengs_backend.services import territory_game as service
from daengs_backend.services.activity_core import first_season_policy as first
from daengs_backend.services.activity_core import game_policy as legacy
from tests.activity.support.database import database as activity_database  # noqa: F401
from tests.activity.test_first_season_db import actor, certify, start
from tests.activity.test_first_season_db import database as reward_database  # noqa: F401
from tests.activity.test_owned_territories_db import Lookup


@pytest.fixture
async def database(reward_database):  # noqa: F811
    return reward_database


async def read(database, method, *args, **kwargs):
    async with database() as db:
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        return await method(db, *args, **kwargs)


async def seed_scores(database, clock, pets, scores, rules=None):
    """Arithmetic fixtures: valid stored score balances, independent of ownership fixtures."""
    async with database() as db:
        season = await activity_game.create_season(
            db, "scores", clock[0] - 10_000, clock[0] + 10_000, rules or first.Rules()
        )
        for pet, score in zip(pets, scores, strict=True):
            db.add(
                ActivityAccount(season_id=season.id, pet_id=pet, score=asdict(score), revision=1)
            )
        await db.commit()
    return season


async def test_snapshot_auth_rejects_withdrawn_and_unknown_members(database, actors):
    (member, _), pets = actors
    assert await read(database, service.require_member, member) is None
    with pytest.raises(service.GameMemberNotActive):
        await read(database, service.require_member, pets[0])
    async with database() as db:
        (await db.get(AppUser, member)).status = "withdrawn"
        await db.commit()
    with pytest.raises(service.GameMemberNotActive):
        await read(database, service.require_member, member)


async def test_exact_ties_keyset_profile_and_final_rank_agree(database, actors, clock):
    (viewer, _), pets = actors
    # A one-unit difference beyond bigint/JS precision must still affect rank, even if
    # both numbers have the same one-decimal presentation. Names never define identity.
    units = 2**65
    scores = [first.Score(holding_units=units + d, last_ms=clock[0]) for d in (1, 1, 0)]
    season = await seed_scores(database, clock, pets, scores)
    async with database() as db:
        for pet in await db.scalars(select(Pet)):
            pet.name = "두부"
        await db.commit()
    items, cursor = [], None
    for _ in pets:
        page = await read(database, service.leaderboard, viewer, limit=1, cursor=cursor)
        assert page["total_count"] == 3
        items.extend(page["items"])
        cursor = page["next_cursor"]
    assert cursor is None
    assert [i["season_record"]["rank"] for i in items] == [1, 1, 3]
    assert [i["pet"]["pet_id"] for i in items[:2]] == sorted(pets[:2])
    assert len({i["season_record"]["points"] for i in items}) == 1
    for item in items:
        profile = await read(database, service.profile, viewer, item["pet"]["pet_id"])
        assert profile["pet"] == item["pet"]
        assert profile["season_record"] == item["season_record"]
    clock[0] = season.ends_ms
    async with database() as db:
        await activity_game.close_if_due(db, clock[0])
        await db.commit()
        for item in items:
            account = await db.get(ActivityAccount, (season.id, item["pet"]["pet_id"]))
            assert account.final_rank == item["season_record"]["rank"]


@pytest.mark.parametrize(
    "rules", [first.Rules(), legacy.Rules(), legacy.Rules(unverified_scores=False)]
)
async def test_sql_scores_match_policy_at_one_confirmed_cut(database, actors, clock, rules):
    (_, _), pets = actors
    calculator = first if isinstance(rules, first.Rules) else legacy
    scores = [
        calculator.Score(current_count=4, scoring_count=3, peak=4, last_ms=clock[0] - 9000),
        calculator.Score(current_count=30, scoring_count=30, peak=30, last_ms=clock[0] - 2000),
        calculator.Score(holding_units=45, last_ms=clock[0]),
    ]
    season = await seed_scores(database, clock, pets, scores, rules)
    async with database() as db:
        total, rows = await repo.leaderboard(db, season, service.rates(season), None, 10)
    assert total == 3
    actual = {r.pet_id: r for r in rows}
    for pet, score in zip(pets, scores, strict=True):
        settled = calculator.settle(score, season.confirmed_ms, rules)
        assert actual[pet].holding_units == settled.holding_units
        assert (
            actual[pet].total_units
            == settled.bonus * legacy.POINT_DENOMINATOR + settled.holding_units
        )


async def test_rank_normalizes_old_balance_and_cursor_rejects_new_revision(database, actors, clock):
    (viewer, _), pets = actors
    scores = [
        first.Score(current_count=1, scoring_count=1, peak=1, last_ms=clock[0] - 10_000),
        first.Score(holding_units=10_000, last_ms=clock[0]),
    ]
    await seed_scores(database, clock, pets[:2], scores)
    page = await read(database, service.leaderboard, viewer, limit=1)
    assert page["items"][0]["pet"]["pet_id"] == pets[0]
    # Pending statistics do not invalidate the authoritative game score.
    assert page["status"] == "READY"
    async with database() as db:
        season = await db.get(ActivitySeason, "scores")
        season.revision += 1
        await db.commit()
    with pytest.raises(service.GameReadChanged, match="leaderboard_changed"):
        await read(database, service.leaderboard, viewer, cursor=page["next_cursor"])


async def test_public_ownership_tracks_takeover_and_excludes_expired_without_worker(
    database, actors, clock, monkeypatch
):
    (a, b), (pa, _, pb) = actors
    await start(database, clock)
    await actor(database, clock, a, pa)
    rival = await actor(database, clock, b, pb)
    sites = await read(database, service.sites, pa, Lookup())
    profile = await read(database, service.profile, b, pa)
    assert sites["total_count"] == profile["season_record"]["owned_site_count"] == 1
    assert not profile["pet"]["is_mine"]
    # Existing private detail endpoint continues to reject a peer's request.
    async with database() as db:
        with pytest.raises(activity.ActivityNotFound):
            await activity.territory_summary(db, b, "first", pa)
    await certify(database, clock, rival, monkeypatch)
    assert (await read(database, service.sites, pa, Lookup()))["total_count"] == 0
    profile = await read(database, service.profile, a, pb)
    assert profile["season_record"]["verified_site_count"] == 1
    site_id = (await read(database, service.sites, pb, Lookup()))["items"][0]["site_id"]
    async with database() as db:
        row = await db.get(TerritoryOccupancy, site_id)
        row.expires_at = datetime.fromtimestamp((clock[0] + 1000) / 1000, UTC)
        await db.commit()
    clock[0] += 1000
    profile = await read(database, service.profile, a, pb)
    assert profile["season_record"]["owned_site_count"] == 0
    assert profile["season_record"]["verified_site_count"] == 0
    assert (await read(database, service.sites, pb, Lookup()))["items"] == []
    async with database() as db:
        assert await db.get(TerritoryOccupancy, site_id) is not None  # Read did not mutate.


async def test_nonparticipants_unknown_deleted_and_new_season_states(database, actors, clock):
    (a, _), (pa, pa2, _) = actors
    await start(database, clock)
    assert (await read(database, service.leaderboard, a))["total_count"] == 0
    with pytest.raises(activity.ActivityNotFound):
        await read(database, service.profile, a, pa2)  # Never played: not a public pet.
    await actor(database, clock, a, pa)
    async with database() as db:
        season = await db.get(ActivitySeason, "first")
        clock[0] = season.ends_ms
    # Even before rollover worker, an ended ACTIVE row is not a current season.
    profile = await read(database, service.profile, a, pa)
    assert profile["status"] == "NO_ACTIVE_SEASON" and profile["season_record"] is None
    assert (await read(database, service.leaderboard, a))["items"] == []
    async with database() as db:
        await activity_game.close_if_due(db, clock[0])
        await db.commit()
    clock[0] += 1000
    await start(database, clock, name="second")
    profile = await read(database, service.profile, a, pa)
    assert profile["status"] == "NOT_PARTICIPATING" and profile["season_record"] is None
    assert (await read(database, service.sites, pa, Lookup()))["items"] == []
    async with database() as db:
        await db.delete(await db.get(Pet, pa))
        await db.commit()
    with pytest.raises(activity.ActivityNotFound):
        await read(database, service.profile, a, pa)


async def test_caregiver_claim_is_visible_on_same_public_dog(database, actors, clock):
    (owner, caregiver), (pet, _, _) = actors
    async with database() as db:
        db.add(PetMember(pet_id=pet, app_user_id=caregiver))
        await db.commit()
    await start(database, clock)
    await actor(database, clock, caregiver, pet)
    profile = await read(database, service.profile, owner, pet)
    sites = await read(database, service.sites, pet, Lookup())
    assert profile["season_record"]["owned_site_count"] == sites["total_count"] == 1
    assert sites["items"][0]["pet_id"] == pet


async def test_repeatable_read_keeps_rank_and_profile_consistent(database, actors, clock):
    (viewer, _), pets = actors
    await seed_scores(database, clock, pets[:2], [first.Score(last_ms=clock[0])] * 2)
    async with database() as db:
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        page = await service.leaderboard(db, viewer)
        async with database() as writer:
            row = await writer.get(ActivityAccount, ("scores", pets[0]))
            row.score = row.score | {"bonus": 100, "base_bonus": 100}
            (await writer.get(ActivitySeason, "scores")).revision += 1
            await writer.commit()
        profile = await service.profile(db, viewer, pets[0])
        original = next(i for i in page["items"] if i["pet"]["pet_id"] == pets[0])
        assert profile["season_record"] == original["season_record"]
    assert (await read(database, service.profile, viewer, pets[0]))["season_record"][
        "points"
    ] == "100"
