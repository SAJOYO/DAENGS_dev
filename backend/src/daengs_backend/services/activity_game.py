"""DEV season/scoring composition. Caller transaction also owns #260 occupancy writes."""

import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime

from daengs_backend.config import settings
from daengs_backend.models.activity import (
    ActivityAccount,
    ActivityBonusKey,
    ActivityGameReceipt,
    ActivityHoldingPeriod,
    ActivitySeason,
)
from daengs_backend.repositories import activity as repo
from daengs_backend.repositories import territory_claim as claim_repo
from daengs_backend.services import territory_claim as claim_rules
from daengs_backend.services.activity_core import game_policy as policy


def now_ms():
    return int(datetime.now(UTC).timestamp() * 1000)


async def acquire(db):
    if settings.activity_game_enabled:
        await repo.barrier(db)


def context(season):
    return policy.SeasonContext(
        season.id, season.starts_ms, season.ends_ms, policy.Rules(**season.rules), season.status
    )


async def create_season(db, season_id, starts_ms, ends_ms, rules: policy.Rules):
    """Explicit administration, imported ownership retains its original protection time."""
    await repo.barrier(db)
    policy.require(await repo.active_season(db) is None, "active_season_exists")
    at = now_ms()
    policy.require(starts_ms <= at < ends_ms, "season_must_cover_now")
    prior = await repo.latest_season(db)
    policy.require(prior is None or starts_ms >= prior.ends_ms, "season_overlap")
    season = ActivitySeason(
        id=season_id,
        starts_ms=starts_ms,
        ends_ms=ends_ms,
        coverage_start_ms=at,
        confirmed_ms=at,
        revision=1,
        status="ACTIVE",
        rules=asdict(rules),
    )
    db.add(season)
    await db.flush()
    counts = {}
    for occupancy in await repo.occupancies(db):
        claim, game = await claim_repo.claim_and_session(db, occupancy.claim_id)
        verified = occupancy.certification == "VERIFIED"
        score = counts.get(claim.pet_id, policy.Score(last_ms=at))
        counts[claim.pet_id] = replace(
            score,
            current_count=score.current_count + 1,
            scoring_count=score.scoring_count + int(verified or rules.unverified_scores),
            peak=score.peak + 1,
        )
        db.add(
            ActivityHoldingPeriod(
                season_id=season_id,
                pet_id=claim.pet_id,
                site_id=occupancy.site_id,
                claim_id=claim.id,
                game_session_id=game.id,
                started_ms=at,
                verified_from_ms=at if verified else None,
                start_order=1,
                origin="IMPORTED",
                takeover=False,
            )
        )
    for pet, score in counts.items():
        db.add(ActivityAccount(season_id=season_id, pet_id=pet, score=asdict(score), revision=1))
    await db.commit()
    return season


async def transition(db, before, after, game, claim, event_id, at_ms):
    if not settings.activity_game_enabled or before == after:
        return after
    season = await repo.active_season(db)
    policy.require(season is not None, "season_not_configured")
    policy.require(at_ms >= season.confirmed_ms, "time_before_confirmed_cut")
    old = before.occupancy
    old_owner = (
        None
        if old is None
        else policy.Ownership(
            old.pet_id,
            old.source_session_id,
            old.source_attempt_id,
            old.certification.value,
            old.occupied_at_millis,
        )
    )
    snapshot = policy.SiteSnapshot(season.id, before.site_id, before.version, old_owner)
    candidate = policy.OwnershipCandidate(
        season.id,
        event_id,
        before.site_id,
        before.version,
        str(claim.pet_id),
        str(game.id),
        str(claim.id),
        after.occupancy.certification.value,
        "PHOTO_VERIFIED" if after.occupancy.certification.value == "VERIFIED" else "MARK",
    )
    receipt = await db.get(ActivityGameReceipt, (season.id, event_id))
    policy.require(receipt is None, "unexpected_reapplied_transition")
    pets = {claim.pet_id} | ({uuid.UUID(old.pet_id)} if old else set())
    accounts = {row.pet_id: row for row in await repo.accounts(db, season.id)}
    scores = {
        str(pet): policy.Score(**accounts[pet].score)
        if pet in accounts
        else policy.Score(last_ms=at_ms)
        for pet in pets
    }
    bonus_key = policy.bonus_key(candidate, at_ms, context(season).rules)
    paid = (
        bonus_key is not None
        and await db.get(
            ActivityBonusKey, (season.id, claim.pet_id, before.site_id, bonus_key.utc_day)
        )
        is not None
    )
    plan = policy.plan_ownership(
        context(season), snapshot, candidate, scores, at_ms=at_ms, bonus_already_paid=paid
    )
    # All business rejection happens before mutation: verified photo visits can survive it.
    season.revision += 1
    season.confirmed_ms = at_ms
    for account in plan.accounts:
        pet = uuid.UUID(account.pet_id)
        row = accounts.get(pet)
        if row is None:
            row = ActivityAccount(
                season_id=season.id, pet_id=pet, score={}, revision=season.revision
            )
            db.add(row)
        row.score = asdict(account.score)
        row.revision = season.revision
    if plan.bonus_key is not None:
        db.add(
            ActivityBonusKey(
                season_id=season.id,
                pet_id=claim.pet_id,
                site_id=before.site_id,
                utc_day=plan.bonus_key.utc_day,
            )
        )
    current = await repo.open_period(db, season.id, before.site_id)
    if plan.kind == "OWNERSHIP_CHANGED":
        policy.require((current is not None) == (old is not None), "holding_source_missing")
        if current:
            current.ended_ms, current.end_order = at_ms, season.revision
            await db.flush()  # release the partial unique open-site constraint first
        db.add(
            ActivityHoldingPeriod(
                season_id=season.id,
                pet_id=claim.pet_id,
                site_id=before.site_id,
                claim_id=claim.id,
                game_session_id=game.id,
                started_ms=at_ms,
                verified_from_ms=at_ms if candidate.certification == "VERIFIED" else None,
                start_order=season.revision,
                origin="ACQUIRED",
                takeover=old is not None,
            )
        )
    elif plan.kind == "CERTIFIED":
        policy.require(current is not None, "holding_source_missing")
        current.verified_from_ms = at_ms
    db.add(
        ActivityGameReceipt(
            season_id=season.id,
            event_id=event_id,
            pet_id=claim.pet_id,
            claim_id=claim.id,
            bonus=plan.bonus,
            at_ms=at_ms,
            kind=plan.kind,
        )
    )
    owner = plan.after.owner
    return claim_rules.ClaimSite(
        before.site_id,
        claim_rules.Occupancy(
            owner.pet_id,
            owner.session_id,
            owner.attempt_id,
            claim_rules.Certification(owner.certification),
            owner.occupied_ms,
        ),
        plan.after.version,
    )


async def close_if_due(db, at_ms):
    season = await repo.active_season(db)
    if season is None:
        return None
    policy.require(at_ms >= season.confirmed_ms, "time_before_confirmed_cut")
    accounts = await repo.accounts(db, season.id)
    if at_ms >= season.ends_ms:
        final = policy.plan_finalization(
            context(season), {str(r.pet_id): policy.Score(**r.score) for r in accounts}, at_ms=at_ms
        )
        scores = {r.pet_id: r.score for r in final.accounts}
        archived = {r.pet_id: r.score for r in final.results}
        season.revision += 1
        for row in accounts:
            row.score = asdict(scores[str(row.pet_id)])
            row.final_score = asdict(archived[str(row.pet_id)])
            row.revision = season.revision
        for period in await repo.periods(db, season.id):
            if period.ended_ms is None:
                period.ended_ms, period.end_order = season.ends_ms, season.revision
        await repo.reset_occupancies(db)
        season.status = "FINALIZED"
    elif at_ms > season.confirmed_ms:
        season.revision += 1
        for row in accounts:
            row.revision = season.revision
    season.confirmed_ms = min(at_ms, season.ends_ms)
    for row in accounts:
        if season.status == "ACTIVE":
            row.score = asdict(
                policy.settle(policy.Score(**row.score), season.confirmed_ms, context(season).rules)
            )
        row.revision = season.revision
    return season
