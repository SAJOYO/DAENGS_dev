"""Same production-plan examples as the APP JVM fixture, plus authority boundaries."""

import csv
from dataclasses import replace
from pathlib import Path

import pytest

from daengs_backend.services.territory_claim import (
    Access,
    Certification,
    ClaimSession,
    ClaimSite,
    PhotoOutcome,
    PhotoStatus,
    SiteInteraction,
    evaluate_access,
    mark,
    resolve_photo,
    resume,
    submit_photo,
)

SESSION = ClaimSession("s1", "u1", "p1")


def ready(session=SESSION, site="A"):
    return SiteInteraction(session.client_session_id, site, Access.READY)


def started():
    return mark(ClaimSite("A"), SESSION, ready(), attempt_id="a1", encounter_id="e1", at_millis=1)


def test_shared_scenario_cycle():
    sites = {key: ClaimSite(key) for key in ("A", "B")}
    attempts = {}
    fixture = Path(__file__).parent / "fixtures/territory-claim-scenarios.tsv"
    with fixture.open(encoding="utf-8") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            key = (row["session"], row["site"])
            old = attempts.get(key)
            site = sites[row["site"]]
            session = ClaimSession(row["session"], f"user-{row['pet']}", row["pet"])
            action = row["action"]
            if action == "mark":
                site, attempt = mark(
                    site,
                    session,
                    ready(session, site.site_id),
                    attempt_id=f"attempt-{len(attempts)}",
                    encounter_id=f"encounter-{key}",
                    at_millis=100,
                    existing=old,
                )
            elif action == "submit":
                attempt = submit_photo(old, row["capture"])
            elif action == "resume":
                attempt = resume(old)
            elif action == "resolve":
                site, attempt = resolve_photo(
                    site,
                    old,
                    row["capture"],
                    PhotoOutcome(row["outcome"]),
                    at_millis=200,
                )
            else:
                pytest.fail(f"unknown fixture action {action}")
            if old:
                assert attempt.attempt_id == old.attempt_id, row["step"]
            sites[site.site_id] = site
            attempts[key] = attempt
            assert site.occupancy.owner_pet_id == row["owner"], row["step"]
            assert site.occupancy.certification == row["certification"], row["step"]
            assert attempt.disposition == row["disposition"], row["step"]
            assert attempt.photo_status == row["photo"], row["step"]
    assert len(attempts) == 5
    assert sites["A"].version == 4


@pytest.mark.parametrize(
    ("overrides", "access", "reason"),
    [
        ({}, Access.READY, None),
        ({"distance_m": 12}, Access.APPROACHING, "OUT_OF_RANGE"),
        ({"recording": False}, Access.UNAVAILABLE, "NOT_RECORDING"),
        ({"trusted_location": False}, Access.UNAVAILABLE, "UNTRUSTED_LOCATION"),
        ({"accuracy_m": 9}, Access.UNAVAILABLE, "UNTRUSTED_LOCATION"),
        ({"distance_m": float("nan")}, Access.UNAVAILABLE, "UNTRUSTED_LOCATION"),
    ],
)
def test_access(overrides, access, reason):
    inputs = {
        "recording": True,
        "trusted_location": True,
        "distance_m": 2,
        "accuracy_m": 1,
        "radius_m": 10,
    }
    result = evaluate_access("s1", "A", **(inputs | overrides))
    assert (result.access, result.reason) == (access, reason)


def test_not_ready_and_representative_identity():
    with pytest.raises(ValueError, match="site_not_ready"):
        mark(
            ClaimSite("A"),
            SESSION,
            replace(ready(), access=Access.APPROACHING),
            attempt_id="a1",
            encounter_id="e1",
            at_millis=1,
        )
    site, attempt = started()
    with pytest.raises(ValueError, match="attempt_identity_conflict"):
        mark(
            site,
            replace(SESSION, claiming_pet_id="p2"),
            ready(),
            attempt_id="a2",
            encounter_id="e2",
            at_millis=2,
            existing=attempt,
        )


def test_old_capture_cannot_certify_reshoot():
    site, attempt = started()
    attempt = submit_photo(attempt, "c1")
    site, attempt = resolve_photo(site, attempt, "c1", PhotoOutcome.REJECTED, at_millis=2)
    attempt = submit_photo(attempt, "c2")
    with pytest.raises(ValueError, match="stale_capture"):
        resolve_photo(site, attempt, "c1", PhotoOutcome.ACCEPTED, at_millis=3)
    assert site.occupancy.certification == Certification.UNVERIFIED


def test_site_version_conflict_keeps_current_owner():
    site, a = started()
    a = submit_photo(a, "c1")
    rival = ClaimSession("s2", "u2", "p2")
    site, b = mark(site, rival, ready(rival), attempt_id="a2", encounter_id="e2", at_millis=2)
    b = submit_photo(b, "c2")
    site, b = resolve_photo(site, b, "c2", PhotoOutcome.ACCEPTED, at_millis=3)
    with pytest.raises(ValueError, match="site_changed"):
        resolve_photo(site, a, "c1", PhotoOutcome.ACCEPTED, at_millis=4)
    assert site.occupancy.owner_pet_id == "p2"


def test_duplicate_accepted_result_preserves_version_and_occupation_time():
    site, attempt = started()
    attempt = submit_photo(attempt, "c1")
    site, attempt = resolve_photo(site, attempt, "c1", PhotoOutcome.ACCEPTED, at_millis=2)
    assert site.occupancy.occupied_at_millis == 1
    assert resolve_photo(site, attempt, "c1", PhotoOutcome.ACCEPTED, at_millis=3) == (site, attempt)
    assert attempt.photo_status == PhotoStatus.VERIFIED


def test_own_unverified_site_can_be_certified_on_later_walk():
    site, _ = started()
    later = replace(SESSION, client_session_id="s2")
    site, attempt = mark(site, later, ready(later), attempt_id="a2", encounter_id="e2", at_millis=2)
    assert site.version == 1
    attempt = submit_photo(attempt, "c1")
    site, attempt = resolve_photo(site, attempt, "c1", PhotoOutcome.ACCEPTED, at_millis=3)
    assert site.occupancy.certification == Certification.VERIFIED
