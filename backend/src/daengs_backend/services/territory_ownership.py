"""Transactional adapters for territory_claim rules. GPS remains client attestation."""

import uuid
from datetime import UTC, datetime, timedelta

from daengs_backend.models.territory_claim import (
    TerritoryClaim,
    TerritoryClaimPhoto,
    TerritoryOccupancy,
)
from daengs_backend.repositories import territory as photos
from daengs_backend.repositories import territory_claim as repo
from daengs_backend.schemas.territory_claim import (
    ClaimResponse,
    OccupancyResponse,
    SessionResponse,
    SiteResponse,
)
from daengs_backend.services import territory_claim as rules

MARK_RADIUS_M = 20.0
CONTACT_MAX_AGE_S = 30


class ClaimNotFound(LookupError):
    pass


class ClaimConflict(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _now():
    return datetime.now(UTC)


def _fresh(observed, now):
    age = (now - observed).total_seconds()
    if age < -5 or age > CONTACT_MAX_AGE_S:
        raise ClaimConflict("stale_location")


def _session_response(row):
    return SessionResponse.model_validate(row, from_attributes=True)


async def start_session(db, owner, client_id, body):
    row = await repo.session_by_client(db, owner, client_id)
    if row is None:
        if body.started_at > _now() + timedelta(seconds=5):
            raise ClaimConflict("future_session")
        if await repo.eligible_pets(db, owner, body.pet_ids) != set(body.pet_ids):
            raise ClaimConflict("ineligible_pet")
        await repo.insert_session(
            db,
            id=uuid.uuid4(),
            app_user_id=owner,
            client_session_id=client_id,
            pet_ids=body.pet_ids,
            started_at=body.started_at,
            phase="RECORDING",
            version=0,
        )
        row = await repo.session_by_client(db, owner, client_id, lock=True)
    if row.started_at != body.started_at or row.pet_ids != body.pet_ids:
        raise ClaimConflict("session_identity_conflict")
    await db.commit()
    return _session_response(row)


async def get_session(db, owner, client_id):
    row = await repo.session_by_client(db, owner, client_id)
    if row is None:
        raise ClaimNotFound
    return _session_response(row)


async def change_phase(db, owner, client_id, body):
    row = await repo.session_by_client(db, owner, client_id, lock=True)
    if row is None:
        raise ClaimNotFound
    # A repeated transition succeeds only at exactly its resulting version.
    if row.version == body.expected_version + 1 and row.phase == body.phase:
        await db.commit()
        return _session_response(row)
    if row.version != body.expected_version:
        raise ClaimConflict("session_changed")
    if row.phase == "ENDED" and body.phase != "ENDED":
        raise ClaimConflict("session_ended")
    if row.phase != body.phase:
        row.phase = body.phase
        row.version += 1
    await db.commit()
    return _session_response(row)


async def list_sites(db, owner, site_ids):
    rows = {row.site_id: row for row in await repo.read_sites(db, site_ids)}
    result = []
    for site_id in dict.fromkeys(site_ids):
        row = rows.get(site_id)
        occupancy = None
        if row and row.pet_id:
            occupancy = OccupancyResponse(
                owner_pet_id=row.pet_id,
                owner_pet_name=row.pet_name,
                is_mine=row.app_user_id == owner,
                certification=row.certification,
                occupied_at=row.occupied_at,
            )
        result.append(
            SiteResponse(
                site_id=site_id,
                version=row.version if row else 0,
                occupancy=occupancy,
            )
        )
    return result


async def _response(db, owner, claim, game_session):
    site = (await list_sites(db, owner, [claim.site_id]))[0]
    return ClaimResponse(
        claim_id=claim.id,
        client_session_id=game_session.client_session_id,
        claiming_pet_id=claim.pet_id,
        disposition=claim.disposition,
        photo_status=claim.photo_status,
        current_photo_id=claim.current_photo_id,
        resolution_code=claim.resolution_code,
        site=site,
    )


async def get_claim(db, owner, claim_id):
    pair = await repo.owned_claim(db, owner, claim_id)
    if pair is None:
        raise ClaimNotFound
    return await _response(db, owner, *pair)


def _rule_session(game_session, pet_id):
    # Internal session UUID scopes identity across users (client UUID alone does not).
    return rules.ClaimSession(str(game_session.id), str(game_session.app_user_id), str(pet_id))


async def _rule_site(db, site):
    occupied = await repo.occupancy(db, site.site_id)
    owner = None
    if occupied:
        source, game_session = await repo.claim_and_session(db, occupied.claim_id)
        owner = rules.Occupancy(
            str(source.pet_id),
            str(game_session.id),
            str(source.id),
            rules.Certification(occupied.certification),
            int(occupied.occupied_at.timestamp() * 1000),
        )
    return rules.ClaimSite(site.site_id, owner, site.version)


async def _save_site(db, row, state):
    row.version = state.version
    if state.occupancy:
        occupied = await repo.occupancy(db, row.site_id)
        if occupied is None:
            occupied = TerritoryOccupancy(site_id=row.site_id)
            db.add(occupied)
        occupied.claim_id = uuid.UUID(state.occupancy.source_attempt_id)
        occupied.certification = state.occupancy.certification.value
        occupied.occupied_at = datetime.fromtimestamp(
            state.occupancy.occupied_at_millis / 1000, UTC
        )


async def mark(db, owner, body, lookup):
    # Network lookup before row locks; validate freshness again after acquiring them.
    game_session = await repo.session_by_client(db, owner, body.client_session_id)
    if game_session is None:
        raise ClaimNotFound
    contact = body.model_dump_json()
    existing = await repo.claim_at(db, game_session.id, body.site_id)
    if existing:
        if existing.contact != contact:
            raise ClaimConflict("attempt_identity_conflict")
        return await _response(db, owner, existing, game_session)
    target = await lookup.find_near_capture(site_id=body.site_id, lat=body.lat, lng=body.lng)
    if target is None:
        raise ClaimConflict("site_not_nearby")
    game_session = await repo.session_by_client(db, owner, body.client_session_id, lock=True)
    site = await repo.lock_site(db, body.site_id)
    existing = await repo.claim_at(db, game_session.id, body.site_id)
    if existing:
        if existing.contact != contact:
            raise ClaimConflict("attempt_identity_conflict")
        result = await _response(db, owner, existing, game_session)
        await db.commit()
        return result
    if body.claiming_pet_id not in game_session.pet_ids or await repo.eligible_pets(
        db, owner, [body.claiming_pet_id]
    ) != {body.claiming_pet_id}:
        raise ClaimConflict("ineligible_pet")
    now = _now()
    _fresh(body.observed_at, now)
    if body.observed_at < game_session.started_at:
        raise ClaimConflict("before_session")
    from daengs_backend.services.territory import _haversine_m

    access = rules.evaluate_access(
        str(game_session.id),
        body.site_id,
        recording=game_session.phase == "RECORDING",
        trusted_location=not body.is_mock,
        distance_m=_haversine_m(body.lat, body.lng, target.lat, target.lng),
        accuracy_m=body.accuracy_m,
        radius_m=MARK_RADIUS_M,
    )
    if access.access != rules.Access.READY:
        raise ClaimConflict(access.reason)
    claim_id = uuid.uuid4()
    state, attempt = rules.mark(
        await _rule_site(db, site),
        _rule_session(game_session, body.claiming_pet_id),
        access,
        attempt_id=str(claim_id),
        encounter_id=str(claim_id),
        at_millis=int(now.timestamp() * 1000),
    )
    claim = TerritoryClaim(
        id=claim_id,
        session_id=game_session.id,
        site_id=body.site_id,
        pet_id=body.claiming_pet_id,
        expected_site_version=attempt.expected_site_version,
        disposition=attempt.disposition.value,
        photo_status=attempt.photo_status.value,
        contact=contact,
        created_at=now,
    )
    db.add(claim)
    await db.flush()
    await _save_site(db, site, state)
    await db.flush()
    result = await _response(db, owner, claim, game_session)
    await db.commit()
    return result


async def bind_photo(db, owner, claim_id, photo_id):
    # Lock order: photo -> session -> site. Worker: photo -> site. Never reverse it.
    photo = await photos.get_owned(db, owner, photo_id, for_update=True)
    pair = await repo.owned_claim(db, owner, claim_id)
    if photo is None or pair is None:
        raise ClaimNotFound
    claim, game_session = pair
    game_session = await repo.session_by_client(
        db, owner, game_session.client_session_id, lock=True
    )
    site = await repo.lock_site(db, claim.site_id)
    claim = await repo.claim_at(db, game_session.id, claim.site_id)
    binding = await repo.photo_binding(db, photo_id)
    if binding:
        if binding.claim_id != claim_id:
            raise ClaimConflict("photo_already_bound")
    else:
        if game_session.phase != "RECORDING":
            raise ClaimConflict("NOT_RECORDING")
        if claim.resolution_code or claim.photo_status not in {
            "NOT_SUBMITTED",
            "REJECTED",
            "RETRY_PENDING",
        }:
            raise ClaimConflict("photo_already_in_progress_or_verified")
        if (
            photo.client_session_id != game_session.client_session_id
            or photo.site_id != claim.site_id
        ):
            raise ClaimConflict("photo_target_mismatch")
        if photo.captured_at < claim.created_at or photo.captured_at > _now():
            raise ClaimConflict("photo_time_mismatch")
        _fresh(photo.captured_at, _now())
        if await repo.eligible_pets(db, owner, [claim.pet_id]) != {claim.pet_id}:
            raise ClaimConflict("ineligible_pet")
        db.add(TerritoryClaimPhoto(photo_id=photo_id, claim_id=claim_id))
        claim.current_photo_id = photo_id
        claim.photo_status = "PENDING"
        await db.flush()
    await _apply_photo(db, photo, site, claim, game_session)
    await db.flush()
    result = await _response(db, owner, claim, game_session)
    await db.commit()
    return result


async def apply_photo_decision(db, photo):
    """Called inside the photo verdict transaction; no commit and no external I/O here."""
    binding = await repo.photo_binding(db, photo.id)
    if binding is None:
        return
    pair = await repo.claim_and_session(db, binding.claim_id)
    if pair is None:
        return
    claim, game_session = pair
    site = await repo.lock_site(db, claim.site_id)
    claim = await repo.claim_at(db, game_session.id, claim.site_id)
    await _apply_photo(db, photo, site, claim, game_session)


async def _apply_photo(db, photo, site, claim, game_session):
    if claim.current_photo_id != photo.id or claim.photo_status != "PENDING":
        return
    if photo.status in {"PENDING_UPLOAD", "VISION_PENDING"}:
        return
    outcome = {
        "VERIFIED": rules.PhotoOutcome.ACCEPTED,
        "REJECTED": rules.PhotoOutcome.REJECTED,
        "FAILED": rules.PhotoOutcome.RETRYABLE_FAILURE,
    }[photo.status]
    if photo.status == "VERIFIED" and photo.verified_visit is None:
        raise ClaimConflict("verified_visit_missing")
    attempt = rules.ClaimAttempt(
        str(claim.id),
        _rule_session(game_session, claim.pet_id),
        claim.site_id,
        str(claim.id),
        claim.expected_site_version,
        rules.Disposition(claim.disposition),
        str(photo.id),
        rules.PhotoStatus.PENDING,
    )
    state = await _rule_site(db, site)
    try:
        updated, resolved = rules.resolve_photo(
            state,
            attempt,
            str(photo.id),
            outcome,
            at_millis=int(_now().timestamp() * 1000),
        )
    except ValueError as exc:
        if str(exc) not in {"site_changed", "new_session_required"}:
            raise
        # The visit stays verified; losing the race must not roll back that fact or retry forever.
        claim.photo_status = "VERIFIED"
        claim.resolution_code = str(exc)
        return
    claim.photo_status = resolved.photo_status.value
    claim.disposition = resolved.disposition.value
    claim.expected_site_version = resolved.expected_site_version
    await _save_site(db, site, updated)
