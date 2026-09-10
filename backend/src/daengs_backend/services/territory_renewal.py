"""Authenticated, fresh onsite renewal for an existing unverified holding."""

from dataclasses import replace

from daengs_backend.models.territory_claim import TerritoryRenewal
from daengs_backend.repositories import territory_claim as repo
from daengs_backend.schemas.territory_claim import RenewalResponse
from daengs_backend.services import activity_game, territory_expiry
from daengs_backend.services import territory_ownership as ownership
from daengs_backend.services.territory import _haversine_m
from daengs_backend.services.territory_claim import Access, evaluate_access


def response(row):
    return RenewalResponse(
        renewal_id=row.id, site_version=row.site_version, expires_at=row.expires_at
    )


def replay(row, claim_id, contact):
    if row.claim_id != claim_id or row.contact != contact:
        raise ownership.ClaimConflict("renewal_identity_conflict")
    return response(row)


async def renew(db, owner, claim_id, renewal_id, body, lookup):
    pair = await repo.owned_claim(db, owner, claim_id)
    if pair is None:
        raise ownership.ClaimNotFound
    contact = body.model_dump_json()
    existing = await db.get(TerritoryRenewal, renewal_id)
    if existing is not None:
        # A committed result is independent of today's GPS/provider availability or ownership.
        return replay(existing, claim_id, contact)
    # External lookup precedes the shared write lock. Freshness is checked again inside it.
    target = await lookup.find_near_capture(site_id=body.site_id, lat=body.lat, lng=body.lng)
    await activity_game.acquire(db)
    pair = await repo.owned_claim(db, owner, claim_id)
    if pair is None:
        raise ownership.ClaimNotFound
    claim, game = pair
    existing = await db.get(TerritoryRenewal, renewal_id)
    if existing is not None:
        result = replay(existing, claim_id, contact)
        await db.commit()
        return result
    season = await ownership._season(db)
    if not territory_expiry.enabled(season):
        raise ownership.ClaimConflict("policy_unavailable")
    game = await repo.session_by_client(db, owner, game.client_session_id, lock=True)
    site = await repo.lock_site(db, claim.site_id)
    now = ownership._now()
    at_ms = int(now.timestamp() * 1000)
    territory_expiry.deadline(at_ms, season)
    await territory_expiry.expire_due(db, season, at_ms)
    if (
        body.client_session_id != game.client_session_id
        or body.site_id != claim.site_id
        or body.claiming_pet_id != claim.pet_id
    ):
        raise ownership.ClaimConflict("target_mismatch")
    if await repo.eligible_pets(db, owner, [claim.pet_id]) != {claim.pet_id}:
        raise ownership.ClaimConflict("ineligible_pet")
    ownership._fresh(body.observed_at, now)
    if body.observed_at < game.started_at:
        raise ownership.ClaimConflict("before_session")
    if target is None:
        raise ownership.ClaimConflict("site_not_nearby")
    access = evaluate_access(
        str(game.id),
        claim.site_id,
        recording=game.phase == "RECORDING",
        trusted_location=not body.is_mock,
        distance_m=_haversine_m(body.lat, body.lng, target.lat, target.lng),
        accuracy_m=body.accuracy_m,
        radius_m=ownership.MARK_RADIUS_M,
    )
    if access.access != Access.READY:
        raise ownership.ClaimConflict(access.reason)
    if site.version != body.expected_site_version:
        raise ownership.ClaimConflict("site_changed")
    before = await ownership._rule_site(db, site)
    if before.occupancy is None or before.occupancy.owner_pet_id != str(claim.pet_id):
        raise ownership.ClaimConflict("not_current_owner")
    if before.occupancy.certification.value == "VERIFIED":
        raise ownership.ClaimConflict("photo_required")
    await ownership._save_site(
        db,
        site,
        replace(before, version=before.version + 1),
        game=game,
        claim=claim,
        event_id="renew:" + str(renewal_id),
        at_ms=at_ms,
    )
    occupied = await repo.occupancy(db, site.site_id)
    renewal = TerritoryRenewal(
        id=renewal_id,
        claim_id=claim_id,
        season_id=season.id,
        contact=contact,
        site_version=site.version,
        created_at=now,
        expires_at=occupied.expires_at,
    )
    db.add(renewal)
    result = response(renewal)
    await db.commit()
    return result
