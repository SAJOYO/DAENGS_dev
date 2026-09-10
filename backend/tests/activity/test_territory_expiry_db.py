"""Real PostgreSQL boundaries for leases, existing rewards and photo decisions."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from daengs_backend.models.activity import (
    ActivityAccount,
    ActivityGameReceipt,
    ActivityHoldingPeriod,
)
from daengs_backend.models.activity_reward import ActivityBaseReward
from daengs_backend.models.territory_claim import TerritoryOccupancy, TerritoryRenewal
from daengs_backend.schemas.territory_claim import RenewalRequest
from daengs_backend.services import activity, activity_game, territory_owner, territory_renewal
from daengs_backend.services.activity_core import first_season_policy as first
from daengs_backend.services.activity_core.game_policy import HOUR_MS, POINT_DENOMINATOR
from daengs_backend.services.territory_expiry import OWNERSHIP_MS
from tests.activity.support.actions import mark
from tests.activity.support.database import database as activity_database  # noqa: F401
from tests.activity.test_first_season_db import actor, certify
from tests.activity.test_first_season_db import database as reward_database  # noqa: F401
from tests.territory.certification.test_territory_certified_db import result, shoot
from tests.territory.support import ownership as base
from tests.territory.support.paths import REPO


@pytest.fixture(autouse=True)
async def database(reward_database):  # noqa: F811 - pytest fixture dependency
    async with reward_database() as db:
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        for path in (
            "db/migrations/2026-09-10_territory_expiry.sql",
            "db/init/30_territory_expiry.sql",
            "db/migrations/verify_2026-09-10_territory_expiry.sql",
        ):
            await raw.execute((REPO / path).read_text("utf-8"))
        await db.commit()
    return reward_database


async def start(database, clock, hours=240):
    async with database() as db:
        return await activity_game.create_season(
            db, "first", clock[0] - 1000, clock[0] + hours * HOUR_MS, first.Rules()
        )


async def renew(database, clock, item, *, renewal_id=None, body=None, **overrides):
    member, client, claim = item
    if body is None:
        current = await result(database, item)
        values = (
            base.mark_body(
                client,
                claim.claiming_pet_id,
                observed_at=datetime.fromtimestamp(clock[0] / 1000, UTC),
            ).model_dump()
            | {"expected_site_version": current.site.version}
            | overrides
        )
        body = RenewalRequest(**values)
    async with database() as db:
        return await territory_renewal.renew(
            db, member, claim.claim_id, renewal_id or uuid.uuid4(), body, base.Lookup()
        )


@pytest.mark.parametrize("verified", [False, True])
async def test_expiry_exact_cut_and_delayed_worker(database, actors, clock, monkeypatch, verified):
    (a, _), (pa, _, _) = actors
    await start(database, clock)
    started = clock[0]
    item = await actor(database, clock, a, pa)
    if verified:
        await certify(database, clock, item, monkeypatch)
    clock[0] = started + OWNERSHIP_MS - 1
    assert (await result(database, item)).site.occupancy is not None
    clock[0] += 1
    async with database() as db:
        public = await territory_owner.summary(db, a, base.SITE)
        assert public["owner"] is None and public["status"] == "UNOCCUPIED"
    assert (await result(database, item)).site.occupancy is None
    # Reads may roll back; a late worker still settles at 72h, not when it eventually runs.
    clock[0] += 20 * HOUR_MS
    async with database() as db:
        await activity.process_pending(db)
    async with database() as db:
        score = (await db.get(ActivityAccount, ("first", pa))).score
        assert score["holding_units"] == 72 * (10 if verified else 2) * POINT_DENOMINATOR
        assert score["current_count"] == score["scoring_count"] == 0
        assert score["bonus"] == (100 if verified else 20)
        assert (await db.get(ActivityBaseReward, ("first", a, base.SITE))).paid == score["bonus"]
        period = await db.scalar(select(ActivityHoldingPeriod))
        assert period.ended_ms == started + OWNERSHIP_MS
        assert await db.get(TerritoryOccupancy, base.SITE) is None
    clock[0] += HOUR_MS
    async with database() as db:
        await activity.process_pending(db)
        assert (await db.get(ActivityAccount, ("first", pa))).score["holding_units"] == score[
            "holding_units"
        ]


async def test_multiple_expiries_split_counts_in_order(database, actors, clock, monkeypatch):
    (a, _), (pa, _, _) = actors
    await start(database, clock)
    first_at = clock[0]
    item = await actor(database, clock, a, pa)
    clock[0] += HOUR_MS
    await mark(database, clock, a, item[1], pa, site_id=base.SITE2)
    clock[0] += HOUR_MS
    await certify(database, clock, item, monkeypatch)  # first: 2h*2 then 72h*10
    clock[0] = first_at + 80 * HOUR_MS
    async with database() as db:
        await activity.process_pending(db)
    async with database() as db:
        score = (await db.get(ActivityAccount, ("first", pa))).score
        assert score["holding_units"] == (2 * 2 + 72 * 10 + 72 * 2) * POINT_DENOMINATOR
        assert score["held_site_ms"] == (74 + 72) * HOUR_MS
        assert score["current_count"] == score["scoring_count"] == 0


async def test_gps_renewal_replay_and_new_walk_are_zero_reward(database, actors, clock):
    (a, _), (pa, _, _) = actors
    await start(database, clock)
    item = await actor(database, clock, a, pa)
    initial = item[2].site.occupancy
    clock[0] += HOUR_MS
    current = await result(database, item)
    body = RenewalRequest(
        **base.mark_body(
            item[1], pa, observed_at=datetime.fromtimestamp(clock[0] / 1000, UTC)
        ).model_dump(),
        expected_site_version=current.site.version,
    )
    renewal_id = uuid.uuid4()
    answers = await asyncio.gather(
        *[renew(database, clock, item, renewal_id=renewal_id, body=body) for _ in range(2)]
    )
    assert answers[0] == answers[1]
    clock[0] += HOUR_MS
    assert await renew(database, clock, item, renewal_id=renewal_id, body=body) == answers[0]
    with pytest.raises(base.svc.ClaimConflict, match="renewal_identity_conflict"):
        await renew(
            database,
            clock,
            item,
            renewal_id=renewal_id,
            body=body.model_copy(update={"accuracy_m": 4}),
        )
    new_walk = await actor(database, clock, a, pa)
    occupied = new_walk[2].site.occupancy
    assert occupied.occupied_at == initial.occupied_at
    assert int(occupied.expires_at.timestamp() * 1000) == clock[0] + OWNERSHIP_MS
    async with database() as db:
        assert (await db.get(ActivityAccount, ("first", pa))).score["bonus"] == 20
        assert await db.scalar(select(func.count()).select_from(TerritoryRenewal)) == 1
        assert await db.scalar(select(func.count()).select_from(ActivityHoldingPeriod)) == 1


async def test_verified_photo_renews_without_resetting_protection(
    database, actors, clock, monkeypatch
):
    (a, _), (pa, _, _) = actors
    await start(database, clock)
    item = await actor(database, clock, a, pa)
    await certify(database, clock, item, monkeypatch)
    before = (await result(database, item)).site.occupancy
    clock[0] += 60_000
    with pytest.raises(base.svc.ClaimConflict, match="photo_required"):
        await renew(database, clock, item)
    # Even a different walk's own-dog photo keeps the original certified/protection time.
    second = await actor(database, clock, a, pa)
    assert second[2].site.occupancy.expires_at == before.expires_at
    photo = await certify(database, clock, second, monkeypatch)
    after = (await result(database, second)).site.occupancy
    assert after.certified_at == before.certified_at
    assert after.protected_until == before.protected_until
    assert after.occupied_at == before.occupied_at
    assert after.expires_at == before.expires_at + timedelta(minutes=1)
    clock[0] += HOUR_MS
    await base.decide(database, photo, monkeypatch)
    assert (await result(database, second)).site.occupancy.expires_at == after.expires_at
    async with database() as db:
        assert (await db.get(ActivityGameReceipt, ("first", "photo:" + str(photo)))).bonus == 0
        assert (await db.get(ActivityAccount, ("first", pa))).score["bonus"] == 100


async def test_expired_reacquisition_keeps_cap_and_has_no_takeover_bonus(
    database, actors, clock, monkeypatch
):
    (a, b), (pa, _, pb) = actors
    await start(database, clock)
    item = await actor(database, clock, a, pa)
    challenger = await actor(database, clock, b, pb)
    clock[0] += OWNERSHIP_MS
    await certify(database, clock, challenger, monkeypatch)
    async with database() as db:
        assert (await db.get(ActivityAccount, ("first", pb))).score["bonus"] == 100
    clock[0] += OWNERSHIP_MS
    await certify(database, clock, item, monkeypatch)
    async with database() as db:
        score = (await db.get(ActivityAccount, ("first", pa))).score
        assert score["base_bonus"] == 100 and score["takeover_bonus"] == 0
        assert (await db.get(ActivityBaseReward, ("first", a, base.SITE))).paid == 100


async def test_photo_crossing_expiry_keeps_visit_but_cannot_revive_owner(
    database, actors, clock, monkeypatch
):
    (a, _), (pa, _, _) = actors
    await start(database, clock)
    item = await actor(database, clock, a, pa)
    clock[0] += OWNERSHIP_MS - 1
    photo = await shoot(database, clock, item)
    clock[0] += 1
    await base.decide(database, photo, monkeypatch)
    current = await result(database, item)
    assert current.photo_status == "VERIFIED" and current.resolution_code == "site_changed"
    assert current.site.occupancy is None
    async with database() as db:
        assert await db.get(ActivityGameReceipt, ("first", "photo:" + str(photo))) is None
        assert (await db.get(ActivityBaseReward, ("first", a, base.SITE))).paid == 20


async def test_season_end_caps_lease_and_final_score(database, actors, clock, monkeypatch):
    (a, _), (pa, _, _) = actors
    season = await start(database, clock, hours=2)
    item = await actor(database, clock, a, pa)
    assert int(item[2].site.occupancy.expires_at.timestamp() * 1000) == season.ends_ms
    clock[0] += HOUR_MS
    await renew(database, clock, item)
    clock[0] = season.ends_ms - 1
    photo = await shoot(database, clock, item)
    clock[0] = season.ends_ms
    await base.decide(database, photo, monkeypatch)
    assert (await result(database, item)).site.occupancy is None
    async with database() as db:
        await activity.process_pending(db)
    async with database() as db:
        account = await db.get(ActivityAccount, ("first", pa))
        assert account.final_score["bonus"] == 20
        assert account.final_score["holding_units"] == 4 * POINT_DENOMINATOR


@pytest.mark.parametrize(
    "bad,code",
    [
        ({"is_mock": True}, "UNTRUSTED_LOCATION"),
        ({"accuracy_m": 30}, "UNTRUSTED_LOCATION"),
        ({"lat": "37.6000000"}, "OUT_OF_RANGE"),
        ({"expected_site_version": 0}, "site_changed"),
    ],
)
async def test_invalid_onsite_renewal_does_not_change_expiry(database, actors, clock, bad, code):
    (a, _), (pa, _, _) = actors
    await start(database, clock)
    item = await actor(database, clock, a, pa)
    clock[0] += HOUR_MS
    with pytest.raises(base.svc.ClaimConflict, match=code):
        await renew(database, clock, item, **bad)
    assert (await result(database, item)).site.occupancy.expires_at == item[
        2
    ].site.occupancy.expires_at


async def test_renewal_storage_failure_rolls_back_extension(database, actors, clock, monkeypatch):
    (a, _), (pa, _, _) = actors
    await start(database, clock)
    item = await actor(database, clock, a, pa)
    clock[0] += HOUR_MS
    original = Session.flush

    def fail(self, *args, **kwargs):
        if any(isinstance(row, TerritoryRenewal) for row in self.new):
            raise RuntimeError("renewal storage failed")
        return original(self, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Session, "flush", fail)
        with pytest.raises(RuntimeError, match="renewal storage failed"):
            await renew(database, clock, item)
    after = await result(database, item)
    assert after.site.version == item[2].site.version
    assert after.site.occupancy == item[2].site.occupancy


async def test_parallel_renewals_and_takeover_resolve_one_site_version(
    database, actors, clock, monkeypatch
):
    (a, b), (pa, _, pb) = actors
    await start(database, clock)
    ai = await actor(database, clock, a, pa)
    bi = await actor(database, clock, b, pb)
    clock[0] += HOUR_MS
    photo = await shoot(database, clock, bi)
    body = RenewalRequest(
        **base.mark_body(
            ai[1], pa, observed_at=datetime.fromtimestamp(clock[0] / 1000, UTC)
        ).model_dump(),
        expected_site_version=ai[2].site.version,
    )
    outcomes = await asyncio.gather(
        renew(database, clock, ai, body=body),
        base.decide(database, photo, monkeypatch),
        return_exceptions=True,
    )
    current = await result(database, bi)
    if isinstance(outcomes[0], base.svc.ClaimConflict):
        assert outcomes[0].code == "site_changed"
        assert current.site.occupancy.owner_pet_id == pb
    else:
        assert not isinstance(outcomes[0], Exception)
        assert current.site.occupancy.owner_pet_id == pa
        assert current.resolution_code == "site_changed"
    assert not isinstance(outcomes[1], Exception)


async def test_expired_mark_reacquisition_and_imported_lease(database, actors, clock, monkeypatch):
    (a, _), (pa, _, _) = actors
    # Existing game-OFF ownership is imported without earning a base reward.
    monkeypatch.setattr(activity.settings, "activity_game_enabled", False)
    item = await actor(database, clock, a, pa)
    assert item[2].site.occupancy.expires_at is None
    clock[0] += HOUR_MS
    monkeypatch.setattr(activity.settings, "activity_game_enabled", True)
    await start(database, clock)
    imported = (await result(database, item)).site.occupancy
    assert imported.occupied_at == item[2].site.occupancy.occupied_at
    assert int(imported.expires_at.timestamp() * 1000) == clock[0] + OWNERSHIP_MS
    clock[0] += OWNERSHIP_MS
    reacquired = await actor(database, clock, a, pa)
    assert reacquired[2].disposition == "GRANTED"
    clock[0] += OWNERSHIP_MS
    again = await actor(database, clock, a, pa)
    assert again[2].disposition == "GRANTED"
    async with database() as db:
        score = (await db.get(ActivityAccount, ("first", pa))).score
        assert score["bonus"] == 20 and score["takeover_bonus"] == 0


async def test_renewal_rejects_peer_stale_paused_expired_and_offline_replays(
    database, actors, clock
):
    (a, b), (pa, _, pb) = actors
    await start(database, clock)
    item = await actor(database, clock, a, pa)
    peer = await actor(database, clock, b, pb)
    with pytest.raises(base.svc.ClaimConflict, match="not_current_owner"):
        await renew(database, clock, peer)
    clock[0] += HOUR_MS
    now = datetime.fromtimestamp(clock[0] / 1000, UTC)
    with pytest.raises(base.svc.ClaimConflict, match="stale_location"):
        await renew(database, clock, item, observed_at=now - timedelta(seconds=31))
    from daengs_backend.schemas.territory_claim import SessionPhase

    async with database() as db:
        await base.svc.change_phase(
            db, a, item[1], SessionPhase(phase="PAUSED", expected_version=0)
        )
    with pytest.raises(base.svc.ClaimConflict, match="NOT_RECORDING"):
        await renew(database, clock, item)
    async with database() as db:
        await base.svc.change_phase(
            db, a, item[1], SessionPhase(phase="RECORDING", expected_version=1)
        )
    current = await result(database, item)
    body = RenewalRequest(
        **base.mark_body(item[1], pa, observed_at=now).model_dump(),
        expected_site_version=current.site.version,
    )
    renewal_id = uuid.uuid4()
    granted = await renew(database, clock, item, renewal_id=renewal_id, body=body)

    class OfflineLookup:
        async def find_near_capture(self, **kwargs):
            raise AssertionError("committed replay must not call provider")

    async with database() as db:
        with pytest.raises(base.svc.ClaimNotFound):
            await territory_renewal.renew(
                db, b, item[2].claim_id, renewal_id, body, OfflineLookup()
            )
    clock[0] += OWNERSHIP_MS
    async with database() as db:
        assert (
            await territory_renewal.renew(
                db, a, item[2].claim_id, renewal_id, body, OfflineLookup()
            )
            == granted
        )
    with pytest.raises(base.svc.ClaimConflict, match="not_current_owner"):
        await renew(database, clock, item)
