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
    ActivityMonthlySeason,
    ActivitySeason,
)
from daengs_backend.models.activity_reward import ActivityBaseReward, ActivityRewardDetail
from daengs_backend.repositories import activity as repo
from daengs_backend.repositories import territory_claim as claim_repo
from daengs_backend.services import activity_monthly, territory_expiry
from daengs_backend.services import territory_claim as claim_rules
from daengs_backend.services.activity_core import first_season_policy as first
from daengs_backend.services.activity_core import first_season_rewards as rewards
from daengs_backend.services.activity_core import game_policy as policy
from daengs_backend.services.activity_core.monthly_calendar import month


def now_ms():
    return int(datetime.now(UTC).timestamp() * 1000)


async def acquire(db):
    if settings.activity_game_enabled:
        await repo.barrier(db)
        await activity_monthly.rollover(db, now_ms())
        await territory_expiry.expire_due(db, await repo.active_season(db), now_ms())


def context(season):
    return policy.SeasonContext(
        season.id,
        season.starts_ms,
        season.ends_ms,
        rules_type(season.rules)(**season.rules),
        season.status,
    )


def rules_type(values):
    return first.Rules if values.get("version") == rewards.REWARD_VERSION else policy.Rules


def engine(season):
    return first if season.rules.get("version") == rewards.REWARD_VERSION else policy


async def create_season(db, season_id, starts_ms, ends_ms, rules: policy.Rules, *, monthly=False):
    """Explicit administration, imported ownership retains its original protection time."""
    await repo.barrier(db)
    policy.require(await repo.active_season(db) is None, "active_season_exists")
    at = now_ms()
    policy.require(starts_ms <= at < ends_ms, "season_must_cover_now")
    prior = await repo.latest_season(db)
    policy.require(prior is None or starts_ms >= prior.ends_ms, "season_overlap")
    if monthly:
        key, _, end = month(starts_ms)
        policy.require(isinstance(rules, first.Rules), "monthly_policy_mismatch")
        policy.require(season_id == key and ends_ms == end, "monthly_boundary_mismatch")
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
    calculator = engine(season)
    for occupancy in await repo.occupancies(db):
        if territory_expiry.enabled(season):
            # Import starts a new lease at activation; historical protection stays unchanged.
            occupancy.expires_at = territory_expiry.deadline(at, season)
        claim, game = await claim_repo.claim_and_session(db, occupancy.claim_id)
        verified = occupancy.certification == "VERIFIED"
        score = counts.get(claim.pet_id, calculator.Score(last_ms=at))
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
    if monthly:
        db.add(ActivityMonthlySeason(season_id=season_id))
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
            old.owner_pet_id,
            old.source_session_id,
            old.source_attempt_id,
            old.certification.value,
            old.occupied_at_millis,
            old.certified_at_millis,
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
    pets = {claim.pet_id} | ({uuid.UUID(old.owner_pet_id)} if old else set())
    accounts = {row.pet_id: row for row in await repo.accounts(db, season.id)}
    calculator = engine(season)
    scores = {
        str(pet): calculator.Score(**accounts[pet].score)
        if pet in accounts
        else calculator.Score(last_ms=at_ms)
        for pet in pets
    }
    reward = None
    previous_member = None
    if calculator is first:
        ledger = await repo.base_reward(db, season.id, game.app_user_id, before.site_id)
        entitlement = rewards.BaseEntitlement(
            rewards.BaseRewardKey(season.id, str(game.app_user_id), before.site_id),
            ledger.paid if ledger is not None else 0,
        )
        if old:
            source = await claim_repo.claim_and_session(db, uuid.UUID(old.source_attempt_id))
            policy.require(source is not None, "member_source_missing")
            previous_member = str(source[1].app_user_id)
        plan, reward = first.plan_ownership(
            context(season),
            snapshot,
            candidate,
            scores,
            at_ms=at_ms,
            entitlement=entitlement,
            previous_member_id=previous_member,
        )
    else:
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
                takeover=old is not None
                and (calculator is not first or previous_member != str(game.app_user_id)),
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
    if reward is not None:
        if ledger is None:
            ledger = ActivityBaseReward(
                season_id=season.id,
                app_user_id=game.app_user_id,
                site_id=before.site_id,
                paid=reward.entitlement.paid,
            )
            db.add(ledger)
        else:
            ledger.paid = reward.entitlement.paid
        # FK ordering is explicit: ORM has no relationship between these audit rows.
        await db.flush()
        db.add(
            ActivityRewardDetail(
                season_id=season.id,
                event_id=event_id,
                app_user_id=game.app_user_id,
                site_id=before.site_id,
                reward_version=reward.receipt.rules.version,
                base_before=reward.receipt.base_before.paid,
                base_after=reward.receipt.base_after.paid,
                base_points=reward.receipt.base_points,
                takeover_points=reward.receipt.takeover_points,
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
            owner.certified_ms,
        ),
        before.version + 1
        if calculator is first and plan.kind == "UNCHANGED"
        else plan.after.version,
    )


async def close_if_due(db, at_ms):
    await activity_monthly.rollover(db, at_ms)
    return await _close_if_due(db, at_ms)


async def _close_if_due(db, at_ms):
    season = await repo.active_season(db)
    if season is None:
        return None
    await territory_expiry.expire_due(db, season, at_ms)
    policy.require(at_ms >= season.confirmed_ms, "time_before_confirmed_cut")
    accounts = await repo.accounts(db, season.id)
    calculator = engine(season)
    if at_ms >= season.ends_ms:
        final = calculator.plan_finalization(
            context(season),
            {str(r.pet_id): calculator.Score(**r.score) for r in accounts},
            at_ms=at_ms,
        )
        scores = {r.pet_id: r.score for r in final.accounts}
        archived = {r.pet_id: r.score for r in final.results}
        ranks = {r.pet_id: r.rank for r in final.results}
        season.revision += 1
        for row in accounts:
            row.score = asdict(scores[str(row.pet_id)])
            row.final_score = asdict(archived[str(row.pet_id)])
            row.final_rank = ranks[str(row.pet_id)]
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
                calculator.settle(
                    calculator.Score(**row.score), season.confirmed_ms, context(season).rules
                )
            )
        row.revision = season.revision
    return season
