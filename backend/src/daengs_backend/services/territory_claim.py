"""Step-1 ownership rules, independent of photo visits, HTTP, storage and scoring.

Inputs must eventually be resolved by trusted session/contact/capture adapters. These
immutable transitions do not authenticate client data or persist ownership. Existing
territory.py remains the photo-visit service; its VERIFIED is not a claim grant.
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from math import isfinite


class Certification(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"


class Access(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    APPROACHING = "APPROACHING"
    READY = "READY"


class Disposition(StrEnum):
    GRANTED = "GRANTED"
    PHOTO_REQUIRED = "PHOTO_REQUIRED"
    POLICY_UNDECIDED = "POLICY_UNDECIDED"
    ALREADY_OWNED = "ALREADY_OWNED"


class PhotoStatus(StrEnum):
    NOT_SUBMITTED = "NOT_SUBMITTED"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    RETRY_PENDING = "RETRY_PENDING"


class PhotoOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"


@dataclass(frozen=True)
class ClaimSession:
    client_session_id: str
    actor_user_id: str
    claiming_pet_id: str

    def __post_init__(self):
        if not all(
            x.strip() for x in (self.client_session_id, self.actor_user_id, self.claiming_pet_id)
        ):
            raise ValueError("empty_session_identity")


@dataclass(frozen=True)
class Occupancy:
    owner_pet_id: str
    source_session_id: str
    source_attempt_id: str
    certification: Certification
    occupied_at_millis: int
    certified_at_millis: int | None = None


@dataclass(frozen=True)
class ClaimSite:
    site_id: str
    occupancy: Occupancy | None = None
    version: int = 0


@dataclass(frozen=True)
class SiteInteraction:
    client_session_id: str
    site_id: str
    access: Access
    reason: str | None = None


@dataclass(frozen=True)
class ClaimAttempt:
    attempt_id: str
    session: ClaimSession
    site_id: str
    encounter_id: str
    expected_site_version: int
    disposition: Disposition
    capture_id: str | None = None
    photo_status: PhotoStatus = PhotoStatus.NOT_SUBMITTED


def evaluate_access(
    session_id: str,
    site_id: str,
    *,
    recording: bool,
    trusted_location: bool,
    distance_m: float,
    accuracy_m: float,
    radius_m: float,
) -> SiteInteraction:
    """No product radius chosen here; the location adapter supplies policy and evidence."""
    if not isfinite(radius_m) or radius_m <= 0:
        raise ValueError("invalid_radius")
    if not recording:
        reason = "NOT_RECORDING"
    elif (
        not trusted_location
        or not isfinite(distance_m)
        or distance_m < 0
        or not isfinite(accuracy_m)
        or accuracy_m < 0
    ):
        reason = "UNTRUSTED_LOCATION"
    elif distance_m > radius_m:
        reason = "OUT_OF_RANGE"
    elif distance_m + accuracy_m > radius_m:
        reason = "UNTRUSTED_LOCATION"
    else:
        reason = None
    access = (
        Access.READY
        if reason is None
        else Access.APPROACHING
        if reason == "OUT_OF_RANGE"
        else Access.UNAVAILABLE
    )
    return SiteInteraction(session_id, site_id, access, reason)


def unverified_disposition(site: ClaimSite, pet_id: str) -> Disposition:
    if site.occupancy is None:
        return Disposition.GRANTED
    if site.occupancy.owner_pet_id == pet_id:
        return Disposition.ALREADY_OWNED
    if site.occupancy.certification == Certification.VERIFIED:
        return Disposition.PHOTO_REQUIRED
    return Disposition.POLICY_UNDECIDED


def mark(
    site: ClaimSite,
    session: ClaimSession,
    interaction: SiteInteraction,
    *,
    attempt_id: str,
    encounter_id: str,
    at_millis: int,
    existing: ClaimAttempt | None = None,
) -> tuple[ClaimSite, ClaimAttempt]:
    """Caller must load existing by (client_session_id, site_id), atomically when persisted."""
    if (
        interaction.client_session_id != session.client_session_id
        or interaction.site_id != site.site_id
    ):
        raise ValueError("target_mismatch")
    if existing is not None:
        if existing.session != session or existing.site_id != site.site_id:
            raise ValueError("attempt_identity_conflict")
        return site, existing
    if interaction.access != Access.READY:
        raise ValueError("site_not_ready")
    if not attempt_id.strip() or not encounter_id.strip():
        raise ValueError("empty_attempt_identity")
    disposition = unverified_disposition(site, session.claiming_pet_id)
    if disposition == Disposition.GRANTED:
        site = replace(
            site,
            version=site.version + 1,
            occupancy=Occupancy(
                session.claiming_pet_id,
                session.client_session_id,
                attempt_id,
                Certification.UNVERIFIED,
                at_millis,
            ),
        )
    return site, ClaimAttempt(
        attempt_id,
        session,
        site.site_id,
        encounter_id,
        site.version,
        disposition,
    )


def resume(attempt: ClaimAttempt) -> ClaimAttempt:
    if attempt.photo_status == PhotoStatus.RETRY_PENDING:
        return replace(attempt, photo_status=PhotoStatus.PENDING)
    return attempt


def submit_photo(attempt: ClaimAttempt, capture_id: str) -> ClaimAttempt:
    """Adapter must bind unique capture to this attempt and validate same-session evidence."""
    if not capture_id.strip():
        raise ValueError("empty_capture")
    if attempt.capture_id == capture_id:
        return resume(attempt)
    if attempt.photo_status not in (PhotoStatus.NOT_SUBMITTED, PhotoStatus.REJECTED):
        raise ValueError("photo_already_in_progress_or_verified")
    return replace(attempt, capture_id=capture_id, photo_status=PhotoStatus.PENDING)


def resolve_photo(
    site: ClaimSite,
    attempt: ClaimAttempt,
    capture_id: str,
    outcome: PhotoOutcome,
    *,
    at_millis: int,
    allow_same_session: bool = False,
) -> tuple[ClaimSite, ClaimAttempt]:
    if site.site_id != attempt.site_id:
        raise ValueError("target_mismatch")
    if attempt.capture_id != capture_id:
        raise ValueError("stale_capture")
    if attempt.photo_status == PhotoStatus.VERIFIED:
        return site, attempt
    if attempt.photo_status != PhotoStatus.PENDING:
        raise ValueError("photo_not_pending")
    if outcome == PhotoOutcome.REJECTED:
        return site, replace(attempt, photo_status=PhotoStatus.REJECTED)
    if outcome == PhotoOutcome.RETRYABLE_FAILURE:
        return site, replace(attempt, photo_status=PhotoStatus.RETRY_PENDING)
    if outcome != PhotoOutcome.ACCEPTED:
        raise ValueError("unknown_photo_outcome")
    # Do not silently choose a delayed/concurrent takeover policy in this foundation.
    if site.version != attempt.expected_site_version:
        raise ValueError("site_changed")
    owner = site.occupancy
    strengthening = owner is not None and owner.source_attempt_id == attempt.attempt_id
    if (
        owner
        and not strengthening
        and not allow_same_session
        and owner.source_session_id == attempt.session.client_session_id
    ):
        raise ValueError("new_session_required")
    site = replace(
        site,
        version=site.version + 1,
        occupancy=Occupancy(
            attempt.session.claiming_pet_id,
            attempt.session.client_session_id,
            attempt.attempt_id,
            Certification.VERIFIED,
            owner.occupied_at_millis if strengthening else at_millis,
            owner.certified_at_millis
            if strengthening and owner.certification == Certification.VERIFIED
            else at_millis,
        ),
    )
    return site, replace(
        attempt,
        disposition=Disposition.GRANTED,
        photo_status=PhotoStatus.VERIFIED,
        expected_site_version=site.version,
    )
