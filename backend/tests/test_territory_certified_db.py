"""V2 admission, race, retry and scoring contracts against production PostgreSQL SQL."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
import test_activity_db as activity_base
import test_territory_ownership_db as base
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, current_app_user
from daengs_backend.main import app
from daengs_backend.models.activity import ActivityAccount, ActivityGameReceipt
from daengs_backend.models.territory_claim import TerritoryChallenge
from daengs_backend.schemas.territory_claim import ChallengeRequest
from daengs_backend.services import activity

territory_database = activity_base.territory_database
database = activity_base.database
actors = base.actors
clock = activity_base.clock
svc = base.svc


async def setup(database, actors, clock):
    await activity_base.season(database, clock, version="certified-protection-v2")
    (a, b), (pa, _, pb) = actors
    sa, sb = await base.begin(database, a, [pa]), await base.begin(database, b, [pb])
    ca = await activity_base.mark(database, clock, a, sa, pa)
    cb = await activity_base.mark(database, clock, b, sb, pb)
    return (a, sa, ca), (b, sb, cb)


async def admit(database, owner, claim, challenge_id=None, expected=None):
    async with database() as db:
        access = await svc.photo_access(db, owner, claim.claim_id)
    async with database() as db:
        return await svc.admit_challenge(
            db,
            owner,
            claim.claim_id,
            challenge_id or uuid.uuid4(),
            ChallengeRequest(
                expected_site_version=access.site_version if expected is None else expected
            ),
        )


async def shoot(database, clock, actor):
    owner, session, claim = actor
    admission = await admit(database, owner, claim)
    return await base.photo(
        database,
        owner,
        session,
        claim,
        captured_at=datetime.fromtimestamp(clock[0] / 1000, UTC),
        capture_id=admission["challenge_id"],
    )


async def result(database, actor):
    async with database() as db:
        return await svc.get_claim(db, actor[0], actor[2].claim_id)


async def test_unverified_immediate_takeover_and_same_walk_retry_boundary(
    database, actors, clock, monkeypatch
):
    a, b = await setup(database, actors, clock)
    assert a[2].site.occupancy.protected_until is None
    assert b[2].disposition == "PHOTO_REQUIRED"
    photo = await shoot(database, clock, b)
    await base.decide(database, photo, monkeypatch)
    first = await result(database, b)
    certified = first.site.occupancy.certified_at
    assert first.site.occupancy.owner_pet_id == b[2].claiming_pet_id
    assert int(certified.timestamp() * 1000) == clock[0]
    clock[0] += 599_999
    with pytest.raises(svc.ClaimConflict, match="protected"):
        await admit(database, a[0], a[2])
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(TerritoryChallenge)) == 1
    clock[0] += 1
    retake = await shoot(database, clock, a)  # Original claim/session, new challenge and photo.
    await base.decide(database, retake, monkeypatch)
    reacquired = await result(database, a)
    assert reacquired.claim_id == a[2].claim_id and reacquired.resolution_code is None
    assert reacquired.site.occupancy.owner_pet_id == a[2].claiming_pet_id
    await base.decide(database, photo, monkeypatch)  # Old photo callback cannot revert ownership.
    assert (await result(database, a)).site == reacquired.site
    async with database() as db:
        account = await db.get(ActivityAccount, ("test", a[2].claiming_pet_id))
        assert account.score["bonus"] == 200 and account.score["takeovers"] == 1
        assert await db.scalar(select(func.count()).select_from(ActivityGameReceipt)) == 3


async def test_own_upgrade_starts_protection_at_verdict_without_bonus_or_renewal(
    database, actors, clock, monkeypatch
):
    a, _ = await setup(database, actors, clock)
    clock[0] += 120_000
    photo = await shoot(database, clock, a)
    clock[0] += 5_000
    await base.decide(database, photo, monkeypatch)
    upgraded = await result(database, a)
    occupancy = upgraded.site.occupancy
    assert occupancy.occupied_at == a[2].site.occupancy.occupied_at
    assert int(occupancy.protected_until.timestamp() * 1000) == clock[0] + 600_000
    with pytest.raises(svc.ClaimConflict, match="already_certified"):
        await admit(database, a[0], a[2])
    clock[0] += 600_000
    with pytest.raises(svc.ClaimConflict, match="already_certified"):
        await admit(database, a[0], a[2])
    async with database() as db:
        score = (await db.get(ActivityAccount, ("test", a[2].claiming_pet_id))).score
        assert score["bonus"] == 100 and score["claims"] == 1


async def test_two_admitted_photos_race_preserves_visit_and_allows_loser_to_retry(
    database, actors, clock, monkeypatch
):
    a, b = await setup(database, actors, clock)
    pa, pb = await shoot(database, clock, a), await shoot(database, clock, b)
    await asyncio.gather(
        base.decide(database, pa, monkeypatch), base.decide(database, pb, monkeypatch)
    )
    ra, rb = await result(database, a), await result(database, b)
    assert ra.photo_status == rb.photo_status == "VERIFIED"
    assert sorted([ra.resolution_code or "won", rb.resolution_code or "won"]) == [
        "site_changed",
        "won",
    ]
    loser = a if ra.resolution_code else b
    clock[0] += 600_000
    retry = await shoot(database, clock, loser)
    await base.decide(database, retry, monkeypatch)
    final = await result(database, loser)
    assert (
        final.resolution_code is None
        and final.site.occupancy.owner_pet_id == loser[2].claiming_pet_id
    )


async def test_admission_identity_expiry_and_server_authority(database, actors, clock):
    a, b = await setup(database, actors, clock)
    admission = await admit(database, a[0], a[2])
    assert await admit(database, a[0], a[2], admission["challenge_id"]) == admission
    with pytest.raises(svc.ClaimConflict, match="challenge_identity_conflict"):
        await admit(database, a[0], a[2], admission["challenge_id"], expected=0)
    with pytest.raises(svc.ClaimNotFound):
        await admit(database, b[0], a[2])
    with pytest.raises(svc.ClaimConflict, match="challenge_required"):
        await base.photo(
            database, b[0], b[1], b[2], captured_at=datetime.fromtimestamp(clock[0] / 1000, UTC)
        )
    clock[0] += 30_001
    with pytest.raises(svc.ClaimConflict, match="challenge_expired"):
        await base.photo(
            database,
            a[0],
            a[1],
            a[2],
            captured_at=datetime.fromtimestamp(clock[0] / 1000, UTC),
            capture_id=admission["challenge_id"],
        )
    assert (await admit(database, a[0], a[2]))["challenge_id"] != admission["challenge_id"]


async def test_season_end_during_verdict_never_grants_territory(
    database, actors, clock, monkeypatch
):
    a, _ = await setup(database, actors, clock)
    photo = await shoot(database, clock, a)
    clock[0] += 86_400_000
    await base.decide(database, photo, monkeypatch)
    resolved = await result(database, a)
    assert resolved.photo_status == "VERIFIED" and resolved.resolution_code == "season_ended"
    async with database() as db:
        assert (await activity.current_season(db))["season"] is None


async def test_current_season_and_photo_access_http_contract(database, actors, clock):
    a, b = await setup(database, actors, clock)

    async def session():
        async with database() as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_app_user] = lambda: AppPrincipal(app_user_id=a[0])
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            current = await http.get("/app/activity/seasons/current")
            assert current.status_code == 200
            assert current.json()["season"]["policy_version"] == "certified-protection-v2"
            access = await http.get(f"/app/territory/claims/{a[2].claim_id}/photo-access")
            assert access.status_code == 200 and access.json()["allowed_action"] == "PHOTO_UPGRADE"
            app.dependency_overrides[current_app_user] = lambda: AppPrincipal(app_user_id=b[0])
            assert (
                await http.get(f"/app/territory/claims/{a[2].claim_id}/photo-access")
            ).status_code == 404
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(current_app_user, None)
