"""Real SQL/locks/rollback tests in a disposable localhost PostgreSQL schema.

Set TERRITORY_TEST_DATABASE_URL to postgresql+asyncpg://postgres@127.0.0.1:55439/claims_test.
No fallback to app settings or the team's shared DB. Production schema files are executed.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, current_app_user
from daengs_backend.main import app
from daengs_backend.models import AppUser, TerritoryAttempt
from daengs_backend.models.territory_claim import TerritoryClaim, TerritoryClaimPhoto
from daengs_backend.schemas.territory_claim import SessionPhase, SessionStart
from daengs_backend.services import territory_ownership as svc
from daengs_backend.services.territory_site_lookup import (
    get_territory_site_lookup,
)
from tests.territory.support.ownership import (
    SITE,
    SITE2,
    Lookup,
    begin,
    decide,
    mark,
    mark_body,
    photo,
)
from tests.territory.support.paths import REPO as ROOT


async def test_two_accounts_mark_certify_takeover_and_new_session(database, actors, monkeypatch):
    (a, b), (a1, a2, b1) = actors
    sa = await begin(database, a, [a1, a2])
    sb = await begin(database, b, [b1])
    ca = await mark(database, a, sa, a2)
    cb = await mark(database, b, sb, b1)
    assert ca.site.occupancy.owner_pet_id == a2
    assert cb.disposition == "POLICY_UNDECIDED"
    pb = await photo(database, b, sb, cb)
    await decide(database, pb, monkeypatch)
    async with database() as db:
        visible = (await svc.list_sites(db, a, [SITE]))[0]
        assert visible.occupancy.owner_pet_id == b1
        assert visible.occupancy.certification == "VERIFIED"
        assert not visible.occupancy.is_mine
    # Old session's already used site cannot be marked again with a new request.
    with pytest.raises(svc.ClaimConflict, match="attempt_identity_conflict"):
        await mark(database, a, sa, a1)
    sa2 = await begin(database, a, [a1, a2])
    ca2 = await mark(database, a, sa2, a1)
    assert ca2.disposition == "PHOTO_REQUIRED"
    pa2 = await photo(database, a, sa2, ca2)
    await decide(database, pa2, monkeypatch)
    async with database() as db:
        result = await svc.get_claim(db, a, ca2.claim_id)
        assert result.site.occupancy.owner_pet_id == a1
        assert result.site.version == 3
        assert result.photo_status == "VERIFIED"


async def test_concurrent_same_request_is_one_claim_and_many_sites_allowed(database, actors):
    (a, _), (a1, _, _) = actors
    client_id = await begin(database, a, [a1])
    body = mark_body(client_id, a1)

    async def send():
        async with database() as db:
            return await svc.mark(db, a, body, Lookup())

    results = await asyncio.gather(send(), send(), send())
    assert len({item.claim_id for item in results}) == 1
    assert {item.site.version for item in results} == {1}
    await mark(database, a, client_id, a1, site_id=SITE2)
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(TerritoryClaim)) == 2


async def test_simultaneous_owners_cannot_both_grant_unverified(database, actors):
    (a, b), (a1, _, b1) = actors
    sa = await begin(database, a, [a1])
    sb = await begin(database, b, [b1])
    results = await asyncio.gather(mark(database, a, sa, a1), mark(database, b, sb, b1))
    assert sorted(r.disposition for r in results) == ["GRANTED", "POLICY_UNDECIDED"]


async def test_delayed_photo_cannot_overwrite_new_owner_and_duplicate_verdict_is_safe(
    database, actors, monkeypatch
):
    (a, b), (a1, _, b1) = actors
    sa = await begin(database, a, [a1])
    sb = await begin(database, b, [b1])
    ca = await mark(database, a, sa, a1)
    cb = await mark(database, b, sb, b1)
    pa = await photo(database, a, sa, ca)
    pb = await photo(database, b, sb, cb)
    await decide(database, pb, monkeypatch)
    await decide(database, pa, monkeypatch)
    await decide(database, pa, monkeypatch)
    async with database() as db:
        result = await svc.get_claim(db, a, ca.claim_id)
        assert result.resolution_code == "site_changed"
        assert result.site.occupancy.owner_pet_id == b1
        assert result.site.version == 2


async def test_phase_version_blocks_reordered_resume_and_ended_session(database, actors):
    (a, _), (a1, _, _) = actors
    client_id = await begin(database, a, [a1])

    async def phase(value, version):
        async with database() as db:
            return await svc.change_phase(
                db, a, client_id, SessionPhase(phase=value, expected_version=version)
            )

    assert (await phase("PAUSED", 0)).version == 1
    assert (await phase("PAUSED", 0)).version == 1
    with pytest.raises(svc.ClaimConflict, match="NOT_RECORDING"):
        await mark(database, a, client_id, a1)
    await phase("RECORDING", 1)
    with pytest.raises(svc.ClaimConflict, match="session_changed"):
        await phase("PAUSED", 0)
    await phase("ENDED", 2)
    with pytest.raises(svc.ClaimConflict, match="session_ended"):
        await phase("RECORDING", 3)


@pytest.mark.parametrize(
    "bad,code",
    [
        ({"is_mock": True}, "UNTRUSTED_LOCATION"),
        ({"accuracy_m": 21}, "UNTRUSTED_LOCATION"),
        ({"lat": "37.5010000"}, "OUT_OF_RANGE"),
        ({"site_id": "territory-site:hex-v1:140:999:999"}, "site_not_nearby"),
        ({"observed_at": datetime(2020, 1, 1, tzinfo=UTC)}, "stale_location"),
    ],
)
async def test_invalid_contact_never_creates_claim(database, actors, bad, code):
    (a, _), (a1, _, _) = actors
    client_id = await begin(database, a, [a1])
    with pytest.raises(svc.ClaimConflict, match=code):
        await mark(database, a, client_id, a1, **bad)
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(TerritoryClaim)) == 0


async def test_participant_and_owner_checks(database, actors):
    (a, b), (a1, a2, b1) = actors
    with pytest.raises(svc.ClaimConflict, match="ineligible_pet"):
        await begin(database, a, [b1])
    sa = await begin(database, a, [a1])
    for pet in (a2, b1):
        with pytest.raises(svc.ClaimConflict, match="ineligible_pet"):
            await mark(database, a, sa, pet)
    ca = await mark(database, a, sa, a1)
    async with database() as db:
        with pytest.raises(svc.ClaimNotFound):
            await svc.get_claim(db, b, ca.claim_id)


@pytest.mark.parametrize(
    "decision,expected", [("rejected", "REJECTED"), ("failed", "RETRY_PENDING")]
)
async def test_failed_photo_can_reshoot_same_claim(
    database, actors, monkeypatch, decision, expected
):
    (a, _), (a1, _, _) = actors
    sa = await begin(database, a, [a1])
    ca = await mark(database, a, sa, a1)
    pa = await photo(database, a, sa, ca)
    await decide(database, pa, monkeypatch, decision)
    async with database() as db:
        result = await svc.get_claim(db, a, ca.claim_id)
        assert result.photo_status == expected
        assert result.site.occupancy.certification == "UNVERIFIED"
    pb = await photo(database, a, sa, ca)
    await decide(database, pb, monkeypatch)
    async with database() as db:
        result = await svc.get_claim(db, a, ca.claim_id)
        assert result.photo_status == "VERIFIED"
        assert await db.scalar(select(func.count()).select_from(TerritoryClaim)) == 1
        assert await db.scalar(select(func.count()).select_from(TerritoryClaimPhoto)) == 2


async def test_bound_verdict_finishes_after_walk_ends_and_account_deletion_clears_owner(
    database, actors, monkeypatch
):
    (a, _), (a1, _, _) = actors
    sa = await begin(database, a, [a1])
    ca = await mark(database, a, sa, a1)
    pa = await photo(database, a, sa, ca)
    async with database() as db:
        await svc.change_phase(db, a, sa, SessionPhase(phase="ENDED", expected_version=0))
    await decide(database, pa, monkeypatch)
    async with database() as db:
        assert (await svc.get_claim(db, a, ca.claim_id)).photo_status == "VERIFIED"
        await db.execute(delete(AppUser).where(AppUser.id == a))
        await db.commit()
    async with database() as db:
        assert (await svc.list_sites(db, uuid.uuid4(), [SITE]))[0].occupancy is None


async def test_real_http_roundtrip_returns_shared_dog_without_owner_private_fields(
    database, actors
):
    (a, b), (a1, _, _) = actors

    async def db_dependency():
        async with database() as db:
            yield db

    app.dependency_overrides[get_session] = db_dependency
    app.dependency_overrides[current_app_user] = lambda: AppPrincipal(app_user_id=a)
    app.dependency_overrides[get_territory_site_lookup] = Lookup
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            client_id = uuid.uuid4()
            response = await client.put(
                f"/app/territory/claim-sessions/{client_id}",
                json={
                    "started_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                    "pet_ids": [str(a1)],
                },
            )
            assert response.status_code == 200, response.text
            response = await client.post(
                "/app/territory/claims", json=mark_body(client_id, a1).model_dump(mode="json")
            )
            assert response.status_code == 200, response.text
            app.dependency_overrides[current_app_user] = lambda: AppPrincipal(app_user_id=b)
            response = await client.get("/app/territory/occupancies", params={"site_ids": SITE})
            assert response.status_code == 200
            occupied = response.json()[0]["occupancy"]
            assert occupied["owner_pet_id"] == str(a1)
            assert occupied["is_mine"] is False
            assert set(occupied) == {
                "owner_pet_id",
                "owner_pet_name",
                "is_mine",
                "certification",
                "occupied_at",
                "certified_at",
                "protected_until",
            }
    finally:
        app.dependency_overrides.clear()


async def test_verdict_and_occupancy_rollback_together(database, actors, monkeypatch):
    (a, _), (a1, _, _) = actors
    sa = await begin(database, a, [a1])
    ca = await mark(database, a, sa, a1)
    pa = await photo(database, a, sa, ca)
    original = svc._save_site

    async def fail_save(*args, **kwargs):
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(svc, "_save_site", fail_save)
    with pytest.raises(RuntimeError, match="injected"):
        await decide(database, pa, monkeypatch)
    async with database() as db:
        assert (await db.get(TerritoryAttempt, pa)).status == "VISION_PENDING"
        result = await svc.get_claim(db, a, ca.claim_id)
        assert result.photo_status == "PENDING"
        assert result.site.version == 1
        assert result.site.occupancy.certification == "UNVERIFIED"
    monkeypatch.setattr(svc, "_save_site", original)
    await decide(database, pa, monkeypatch)
    async with database() as db:
        assert (await svc.get_claim(db, a, ca.claim_id)).site.occupancy.certification == "VERIFIED"


async def test_concurrent_certifications_have_one_winner(database, actors, monkeypatch):
    (a, b), (a1, _, b1) = actors
    sa = await begin(database, a, [a1])
    sb = await begin(database, b, [b1])
    ca = await mark(database, a, sa, a1)
    cb = await mark(database, b, sb, b1)
    pa = await photo(database, a, sa, ca)
    pb = await photo(database, b, sb, cb)
    await asyncio.gather(decide(database, pa, monkeypatch), decide(database, pb, monkeypatch))
    async with database() as db:
        results = [await svc.get_claim(db, a, ca.claim_id), await svc.get_claim(db, b, cb.claim_id)]
        assert sum(r.resolution_code == "site_changed" for r in results) == 1
        assert {r.site.version for r in results} == {2}


async def test_photo_must_match_site_session_and_new_capture(database, actors):
    (a, _), (a1, _, _) = actors
    sa = await begin(database, a, [a1])
    ca = await mark(database, a, sa, a1)
    for client_id, options, code in (
        (sa, {"site_id": SITE2}, "photo_target_mismatch"),
        (uuid.uuid4(), {}, "photo_target_mismatch"),
        (sa, {"captured_at": datetime.now(UTC) - timedelta(minutes=1)}, "photo_time_mismatch"),
    ):
        with pytest.raises(svc.ClaimConflict, match=code):
            await photo(database, a, client_id, ca, **options)
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(TerritoryClaimPhoto)) == 0
    pa = await photo(database, a, sa, ca)
    ca2 = await mark(database, a, sa, a1, site_id=SITE2)
    async with database() as db:
        with pytest.raises(svc.ClaimConflict, match="photo_already_bound"):
            await svc.bind_photo(db, a, ca2.claim_id, pa)


async def test_existing_data_survives_migration_replay(database, actors):
    (a, _), (a1, _, _) = actors
    sa = await begin(database, a, [a1])
    ca = await mark(database, a, sa, a1)
    async with database() as db:
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        await raw.execute(
            (ROOT / "db/migrations/2026-09-05_territory_claims.sql").read_text(encoding="utf-8")
        )
        await db.commit()
    async with database() as db:
        result = await svc.get_claim(db, a, ca.claim_id)
        assert result.site.occupancy.owner_pet_id == a1
        assert result.site.version == 1


async def test_session_retry_cannot_change_participants_or_restart_ended_session(database, actors):
    (a, _), (a1, a2, _) = actors
    sa = await begin(database, a, [a1])
    async with database() as db:
        initial = await svc.get_session(db, a, sa)
        await svc.change_phase(db, a, sa, SessionPhase(phase="ENDED", expected_version=0))
    async with database() as db:
        again = await svc.start_session(
            db, a, sa, SessionStart(started_at=initial.started_at, pet_ids=[a1])
        )
        assert again.phase == "ENDED"
    async with database() as db:
        with pytest.raises(svc.ClaimConflict, match="session_identity_conflict"):
            await svc.start_session(
                db, a, sa, SessionStart(started_at=initial.started_at, pet_ids=[a2])
            )


@pytest.mark.parametrize(
    "damage",
    [
        "DROP TABLE territory_claim_photos",
        "ALTER TABLE territory_claims DROP COLUMN resolution_code",
        "ALTER TABLE territory_claims ALTER COLUMN contact TYPE varchar(2048)",
        "ALTER TABLE territory_claims ALTER COLUMN created_at DROP NOT NULL",
        "ALTER TABLE territory_claim_photos DROP CONSTRAINT territory_claim_photos_pkey",
        "ALTER TABLE territory_claims DROP CONSTRAINT territory_claims_pet_id_fkey",
        "ALTER TABLE territory_claims DROP CONSTRAINT territory_claims_disposition_check",
        "ALTER TABLE territory_claims DROP CONSTRAINT territory_claims_session_id_site_id_key",
        "DROP INDEX territory_claims_pet_idx",
        "DROP INDEX ix_territory_claim_photos_claim_id",
        (
            "ALTER TABLE territory_claims DROP CONSTRAINT territory_claims_pet_id_fkey; "
            "ALTER TABLE territory_claims ADD FOREIGN KEY(pet_id) REFERENCES pets(id)"
        ),
    ],
)
async def test_verification_rejects_schema_damage(database, damage):
    verifier = (ROOT / "db/migrations/verify_2026-09-05_territory_claims.sql").read_text(
        encoding="utf-8"
    )
    async with database() as db:
        connection = await db.connection()
        raw = (await connection.get_raw_connection()).driver_connection
        await raw.execute(damage)
        with pytest.raises(asyncpg.PostgresError, match="mismatch|missing table"):
            await raw.execute(verifier)
        await db.rollback()
