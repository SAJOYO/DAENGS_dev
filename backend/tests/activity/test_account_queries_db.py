"""Bound account loading during actual claims, without changing score/season semantics."""

import uuid
from contextlib import contextmanager
from dataclasses import asdict

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from daengs_backend.models import Pet
from daengs_backend.models.activity import ActivityAccount
from daengs_backend.repositories import activity as repo
from daengs_backend.services import activity_game
from daengs_backend.services.activity_core import first_season_policy as first
from daengs_backend.services.activity_core import game_policy as legacy
from tests.activity.support.actions import certify, mark
from tests.activity.support.database import database as activity_database  # noqa: F401
from tests.activity.test_first_season_db import database as reward_database  # noqa: F401
from tests.territory.certification.test_territory_certified_db import shoot
from tests.territory.support import ownership as base


@pytest.fixture
def database(reward_database):  # noqa: F811 - imported pytest fixture dependency
    return reward_database


@contextmanager
def loaded_accounts():
    loaded = []

    def on_load(session, instance):
        if isinstance(instance, ActivityAccount):
            loaded.append(instance.pet_id)

    event.listen(Session, "loaded_as_persistent", on_load)
    try:
        yield loaded
    finally:
        event.remove(Session, "loaded_as_persistent", on_load)


@pytest.mark.parametrize("unrelated", [0, 250])
@pytest.mark.parametrize("policy", [legacy, first], ids=["legacy", "first"])
async def test_claims_load_only_affected_accounts_and_finalization_still_reads_everyone(
    database, actors, clock, monkeypatch, unrelated, policy
):
    (a, b), (pa, _, pb) = actors
    rules = legacy.Rules(version="draft-2026-09-06") if policy is legacy else first.Rules()
    async with database() as db:
        season = await activity_game.create_season(
            db, "scope", clock[0] - 1000, clock[0] + 86_400_000, rules
        )
        others = [uuid.uuid4() for _ in range(unrelated)]
        db.add_all([Pet(id=pet, app_user_id=a, name="unrelated", breed="mixed") for pet in others])
        await db.flush()
        db.add_all(
            [
                ActivityAccount(
                    season_id=season.id,
                    pet_id=pet,
                    score=asdict(policy.Score(last_ms=clock[0])),
                    revision=1,
                )
                for pet in others
            ]
        )
        await db.commit()

    sa, sb = await base.begin(database, a, [pa]), await base.begin(database, b, [pb])
    with loaded_accounts() as loaded:
        owned = await mark(database, clock, a, sa, pa)
    assert loaded == []  # A missing account is created; unrelated rows are never materialized.

    async def certify_claim(owner, client, claim):
        if policy is first:
            photo = await shoot(database, clock, (owner, client, claim))
            await base.decide(database, photo, monkeypatch)
            return photo
        return await certify(database, clock, owner, client, claim, monkeypatch)

    with loaded_accounts() as loaded:
        await certify_claim(a, sa, owned)
    assert loaded == [pa]  # Same-dog certification does not duplicate the lookup.

    # Give the challenger an existing account to exercise both sides of the transfer.
    async with database() as db:
        db.add(
            ActivityAccount(
                season_id=season.id,
                pet_id=pb,
                score=asdict(policy.Score(last_ms=clock[0])),
                revision=1,
            )
        )
        await db.commit()
    clock[0] += 600_000
    challenger = await mark(database, clock, b, sb, pb)
    with loaded_accounts() as loaded:
        photo = await certify_claim(b, sb, challenger)
    assert set(loaded) == {pa, pb} and len(loaded) == 2
    with loaded_accounts() as loaded:
        await base.decide(database, photo, monkeypatch)
    assert loaded == []

    async with database() as db:
        old, new = (
            await db.get(ActivityAccount, (season.id, pa)),
            await db.get(ActivityAccount, (season.id, pb)),
        )
        assert old.score["current_count"] == 0 and new.score["current_count"] == 1
        assert new.score["bonus"] == (120 if policy is first else 100)
        assert (
            await base.svc.get_claim(db, b, challenger.claim_id)
        ).site.occupancy.owner_pet_id == pb

    clock[0] = season.ends_ms
    async with database() as db:
        await activity_game.acquire(db)
        await activity_game.close_if_due(db, clock[0])
        await db.commit()
    async with database() as db:
        rows = await repo.accounts(db, season.id)
        assert len(rows) == unrelated + 2
        assert all(row.final_rank is not None and row.final_score is not None for row in rows)


async def test_empty_pet_selection_never_means_all_accounts(database, actors, clock):
    (owner, _), (pet, _, _) = actors
    async with database() as db:
        await activity_game.create_season(
            db, "scope", clock[0] - 1000, clock[0] + 86_400_000, first.Rules()
        )
    client = await base.begin(database, owner, [pet])
    await mark(database, clock, owner, client, pet)
    async with database() as db:
        assert await repo.accounts_for_pets(db, "scope", set()) == []
        assert await repo.accounts_for_pets(db, "other-season", {pet}) == []
        rows = await repo.accounts_for_pets(db, "scope", [pet, pet, uuid.uuid4()])
        assert [row.pet_id for row in rows] == [pet]
        assert len(list(await db.scalars(select(ActivityAccount)))) == 1
