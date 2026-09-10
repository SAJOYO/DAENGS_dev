"""Season and timed claim builders shared by activity and certification."""

from datetime import UTC, datetime

from daengs_backend.services import activity_game
from daengs_backend.services.activity_core import game_policy as policy
from tests.territory.support import ownership as base


async def season(database, clock, name="test", **rule_values):
    # Preserve the already-running season contract; v2 scenarios opt in explicitly.
    rule_values.setdefault("version", "draft-2026-09-06")
    async with database() as db:
        return await activity_game.create_season(
            db, name, clock[0] - 1000, clock[0] + 86_400_000, policy.Rules(**rule_values)
        )


async def mark(database, clock, owner, client, pet, **overrides):
    return await base.mark(
        database,
        owner,
        client,
        pet,
        observed_at=datetime.fromtimestamp(clock[0] / 1000, UTC),
        **overrides,
    )


async def certify(database, clock, owner, client, claim, monkeypatch):
    photo = await base.photo(
        database, owner, client, claim, captured_at=datetime.fromtimestamp(clock[0] / 1000, UTC)
    )
    await base.decide(database, photo, monkeypatch)
    return photo
