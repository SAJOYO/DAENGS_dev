"""Transactional adapters for territory_claim rules. GPS remains client attestation."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from daengs_backend.models.territory_claim import (
    TerritoryChallenge,
    TerritoryClaim,
    TerritoryClaimPhoto,
    TerritoryOccupancy,
)
from daengs_backend.repositories import territory as photos
from daengs_backend.repositories import territory_claim as repo
from daengs_backend.schemas.territory_claim import (
    ClaimResponse,
    OccupancyResponse,
    PhotoAccessResponse,
    SessionResponse,
    SiteResponse,
)
from daengs_backend.services import activity, activity_game
from daengs_backend.services import territory_claim as rules
from daengs_backend.services.activity_core.game_policy import GameError

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
    await activity_game.acquire(db)
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
    await activity.record_game(db, row)
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
    await activity_game.acquire(db)
    season = await _season(db)
    now = _now()
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
                certified_at=getattr(row, "certified_at", None),
                protected_until=_protection(
                    row.certification, row.occupied_at, getattr(row, "certified_at", None), season
                ),
            )
        result.append(
            SiteResponse(
                site_id=site_id,
                server_now=now,
                season_id=season.id if season else None,
                policy_version=season.rules["version"] if season else None,
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
            int(occupied.certified_at.timestamp() * 1000) if occupied.certified_at else None,
        )
    return rules.ClaimSite(site.site_id, owner, site.version)


async def _save_site(db, row, state, *, game=None, claim=None, event_id=None, at_ms=None):
    if activity.settings.activity_game_enabled:
        state = await activity_game.transition(
            db, await _rule_site(db, row), state, game, claim, event_id, at_ms
        )
    row.version = state.version
    if state.occupancy:
        occupied = await repo.occupancy(db, row.site_id)
        if occupied is None:
            occupied = TerritoryOccupancy(site_id=row.site_id)
            db.add(occupied)
        occupied.claim_id = uuid.UUID(state.occupancy.source_attempt_id)
        occupied.certification = state.occupancy.certification.value
        occupied.certified_at = (
            datetime.fromtimestamp(state.occupancy.certified_at_millis / 1000, UTC)
            if state.occupancy.certified_at_millis is not None
            else None
        )
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
    await activity_game.acquire(db)
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
    if _is_v2(await _season(db)) and attempt.disposition == rules.Disposition.POLICY_UNDECIDED:
        attempt = replace(attempt, disposition=rules.Disposition.PHOTO_REQUIRED)
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
    await _save_site(
        db,
        site,
        state,
        game=game_session,
        claim=claim,
        event_id="mark:" + str(claim.id),
        at_ms=int(now.timestamp() * 1000),
    )
    await db.flush()
    result = await _response(db, owner, claim, game_session)
    await db.commit()
    return result


async def bind_photo(db, owner, claim_id, photo_id):
    # Enabled writers: activity barrier -> photo -> session -> site.
    await activity_game.acquire(db)
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
        challenge = (
            await db.get(TerritoryChallenge, photo.client_capture_id)
            if activity.settings.activity_game_enabled
            else None
        )
        season = await _season(db)
        v2 = _is_v2(season)
        if challenge is not None and (season is None or challenge.season_id != season.id):
            raise ClaimConflict("season_ended")
        if v2:
            if challenge is None or challenge.claim_id != claim_id:
                raise ClaimConflict("challenge_required")
            if challenge.completed_at is not None or challenge.expires_at <= _now():
                raise ClaimConflict("challenge_expired")
            if photo.captured_at < challenge.created_at - timedelta(seconds=5):
                raise ClaimConflict("photo_time_mismatch")
            if challenge.expected_site_version != site.version:
                raise ClaimConflict("site_changed")
            access = await _check_photo_access(db, owner, claim, game_session)
            if access.reason:
                raise ClaimConflict(access.reason)
        if not v2 and (
            claim.resolution_code
            or claim.photo_status
            not in {
                "NOT_SUBMITTED",
                "REJECTED",
                "RETRY_PENDING",
            }
        ):
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
        if v2:
            challenge.photo_id = photo_id
            claim.expected_site_version = challenge.expected_site_version
            claim.resolution_code = None
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
    await activity_game.acquire(db)
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
    challenge = (
        await db.get(TerritoryChallenge, photo.client_capture_id)
        if activity.settings.activity_game_enabled
        else None
    )
    if challenge is not None:
        if (
            challenge.claim_id != claim.id
            or challenge.photo_id != photo.id
            or challenge.completed_at is not None
        ):
            return
        season = await _season(db)
        if season is None or season.id != challenge.season_id:
            claim.photo_status = "VERIFIED" if photo.status == "VERIFIED" else "REJECTED"
            claim.resolution_code = "season_ended"
            challenge.resolution_code, challenge.completed_at = "season_ended", _now()
            return
        attempt = replace(attempt, expected_site_version=challenge.expected_site_version)
        challenge.completed_at = _now()
    state = await _rule_site(db, site)
    at_ms = int(_now().timestamp() * 1000)
    try:
        updated, resolved = rules.resolve_photo(
            state,
            attempt,
            str(photo.id),
            outcome,
            at_millis=at_ms,
            allow_same_session=challenge is not None,
        )
    except ValueError as exc:
        if str(exc) not in {"site_changed", "new_session_required"}:
            raise
        # The visit stays verified; losing the race must not roll back that fact or retry forever.
        claim.photo_status = "VERIFIED"
        claim.resolution_code = str(exc)
        if challenge is not None:
            challenge.resolution_code = str(exc)
        return
    try:
        await _save_site(
            db,
            site,
            updated,
            game=game_session,
            claim=claim,
            event_id="photo:" + str(photo.id),
            at_ms=at_ms,
        )
    except GameError as exc:
        if str(exc) not in {"protected", "season_ended", "new_session_required"}:
            raise
        claim.photo_status = "VERIFIED"
        claim.resolution_code = str(exc)
        if challenge is not None:
            challenge.resolution_code = str(exc)
        return
    claim.photo_status = resolved.photo_status.value
    claim.disposition = resolved.disposition.value
    claim.expected_site_version = site.version


async def _season(db):
    if not activity.settings.activity_game_enabled:
        return None
    return await activity_game.repo.active_season(db)


def _is_v2(season):
    return season is not None and season.rules.get("version") == "certified-protection-v2"


def _protection(certification, occupied_at, certified_at, season):
    if season is None:
        return None
    if _is_v2(season):
        if certification != "VERIFIED":
            return None
        return (certified_at or occupied_at) + timedelta(milliseconds=season.rules["protection_ms"])
    return occupied_at + timedelta(milliseconds=season.rules["protection_ms"])


async def _check_photo_access(db, owner, claim, game):
    site = (await list_sites(db, owner, [claim.site_id]))[0]
    now = _now()
    season = await _season(db)
    reason, action = None, "PHOTO_TAKEOVER"
    if not _is_v2(season):
        reason, action = "policy_unavailable", "UNAVAILABLE"
    elif not season.starts_ms <= int(now.timestamp() * 1000) < season.ends_ms:
        reason, action = "season_ended", "UNAVAILABLE"
    elif game.phase != "RECORDING":
        reason, action = "NOT_RECORDING", "UNAVAILABLE"
    elif await repo.eligible_pets(db, owner, [claim.pet_id]) != {claim.pet_id}:
        reason, action = "ineligible_pet", "UNAVAILABLE"
    elif claim.photo_status == "PENDING":
        reason, action = "photo_already_in_progress_or_verified", "UNAVAILABLE"
    elif site.occupancy:
        if site.occupancy.owner_pet_id == claim.pet_id:
            if site.occupancy.certification == "VERIFIED":
                reason, action = "already_certified", "ALREADY_CERTIFIED"
            else:
                action = "PHOTO_UPGRADE"
        elif site.occupancy.protected_until and now < site.occupancy.protected_until:
            reason, action = "protected", "WAIT"
    return PhotoAccessResponse(
        server_now=now,
        site_version=site.version,
        season_id=site.season_id,
        policy_version=site.policy_version,
        allowed_action=action,
        reason=reason,
        protected_until=site.occupancy.protected_until if site.occupancy else None,
    )


async def photo_access(db, owner, claim_id):
    await activity_game.acquire(db)
    pair = await repo.owned_claim(db, owner, claim_id)
    if pair is None:
        raise ClaimNotFound
    return await _check_photo_access(db, owner, *pair)


async def admit_challenge(db, owner, claim_id, challenge_id, body):
    await activity_game.acquire(db)
    pair = await repo.owned_claim(db, owner, claim_id)
    if pair is None:
        raise ClaimNotFound
    claim, game = pair
    game = await repo.session_by_client(db, owner, game.client_session_id, lock=True)
    await repo.lock_site(db, claim.site_id)
    existing = await db.get(TerritoryChallenge, challenge_id)
    if existing:
        if (
            existing.claim_id != claim_id
            or existing.expected_site_version != body.expected_site_version
        ):
            raise ClaimConflict("challenge_identity_conflict")
        # Admission replay is not a new authorization; expired or completed admissions cannot be shot again.
        if existing.expires_at < _now() or existing.completed_at is not None:
            raise ClaimConflict("challenge_expired")
        return {"challenge_id": existing.id, "expires_at": existing.expires_at}
    access = await _check_photo_access(db, owner, claim, game)
    if access.reason is not None:
        raise ClaimConflict(access.reason)
    if access.site_version != body.expected_site_version:
        raise ClaimConflict("site_changed")
    now = _now()
    challenge = TerritoryChallenge(
        id=challenge_id,
        claim_id=claim_id,
        expected_site_version=access.site_version,
        season_id=access.season_id,
        created_at=now,
        expires_at=now + timedelta(seconds=30),
    )
    db.add(challenge)
    await db.commit()
    return {"challenge_id": challenge.id, "expires_at": challenge.expires_at}
