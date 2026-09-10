"""Compose GEO #264 reward arithmetic with existing certified ownership admission.

The legacy policy stays byte-compatible for historical seasons. scoring_count is
the VERIFIED count in this version; current_count includes both certifications.
"""

from dataclasses import asdict, dataclass, fields, replace

from . import first_season_rewards as rewards
from . import game_policy as legacy


@dataclass(frozen=True)
class Rules(rewards.RewardRules):
    protection_ms: int = 600_000

    def __post_init__(self):
        super().__post_init__()
        legacy.require(
            type(self.protection_ms) is int and self.protection_ms == 600_000,
            "protection_must_be_ten_minutes",
        )

    @property
    def unverified_scores(self):
        # The legacy count allocator tracks verified holdings only in this version.
        return False


@dataclass(frozen=True)
class Score(legacy.Score):
    base_bonus: int = 0
    takeover_bonus: int = 0

    def __post_init__(self):
        legacy.require(
            all(type(value) is int and value >= 0 for value in asdict(self).values()),
            "invalid_first_season_score",
        )
        legacy.require(
            self.bonus == self.base_bonus + self.takeover_bonus, "reward_breakdown_mismatch"
        )
        legacy.require(
            0 <= self.scoring_count <= self.current_count <= self.peak, "invalid_score_counts"
        )


def _admission(season):
    # Reuse protection/CAS/count transitions, with ALL legacy reward arithmetic off.
    return replace(
        season,
        rules=legacy.Rules(
            version="certified-protection-v2",
            claim_points=0,
            takeover_points=0,
            hourly_points=0,
            extra_site_bps=0,
            maximum_bps=10_000,
            unverified_scores=False,
        ),
    )


def settle(score, at_ms, rules):
    balance = rewards.settle_holding(
        rewards.HoldingScore(
            unverified_count=score.current_count - score.scoring_count,
            verified_count=score.scoring_count,
            holding_units=score.holding_units,
            held_site_ms=score.held_site_ms,
            last_ms=score.last_ms,
        ),
        at_ms,
        rules=rules,
    )
    return replace(
        score,
        holding_units=balance.holding_units,
        held_site_ms=balance.held_site_ms,
        last_ms=balance.last_ms,
    )


def plan_ownership(season, site, candidate, scores, *, at_ms, entitlement, previous_member_id):
    legacy.validate_context(_admission(season), site, candidate, at_ms)
    legacy.require((site.owner is None) == (previous_member_id is None), "member_source_missing")
    ready = {}
    for pet, score in scores.items():
        legacy.require(season.starts_ms <= score.last_ms <= at_ms, "invalid_score_time")
        ready[pet] = settle(score, at_ms, season.rules)
    plain = {
        pet: legacy.Score(**{f.name: getattr(score, f.name) for f in fields(legacy.Score)})
        for pet, score in ready.items()
    }
    plan = legacy.plan_ownership(_admission(season), site, candidate, plain, at_ms=at_ms)
    event = rewards.RewardEvent(
        key=entitlement.key,
        event_id=candidate.event_id,
        pet_id=candidate.pet_id,
        certification=plan.after.owner.certification,
        kind={"OWNERSHIP_CHANGED": "ACQUIRED", "CERTIFIED": "CERTIFIED", "UNCHANGED": "RENEWED"}[
            plan.kind
        ],
        previous_member_id=previous_member_id,
        previous_certification=site.owner.certification if site.owner else None,
    )
    legacy.require(
        event.key.season_id == season.season_id and event.key.site_id == site.site_id,
        "base_reward_scope_mismatch",
    )
    reward = rewards.plan_reward(event, entitlement, rules=season.rules)
    accounts = []
    for account in plan.accounts:
        values = asdict(account.score)
        prior = ready[account.pet_id]
        incoming = account.pet_id == candidate.pet_id
        values["bonus"] += reward.credit_points if incoming else 0
        if incoming and site.owner and previous_member_id == entitlement.key.member_id:
            # Switching dogs is an acquisition, never a takeover from another member.
            values["takeovers"] = prior.takeovers
        accounts.append(
            legacy.Account(
                account.pet_id,
                Score(
                    **values,
                    base_bonus=prior.base_bonus + (reward.receipt.base_points if incoming else 0),
                    takeover_bonus=prior.takeover_bonus
                    + (reward.receipt.takeover_points if incoming else 0),
                ),
            )
        )
    return replace(plan, accounts=tuple(accounts), bonus=reward.credit_points), reward


def plan_finalization(season, scores, *, at_ms):
    legacy.require(at_ms >= season.ends_ms, "season_not_ended")
    ready = {pet: settle(score, season.ends_ms, season.rules) for pet, score in scores.items()}
    plan = legacy.plan_finalization(_admission(season), ready, at_ms=at_ms)
    return replace(plan, season=replace(season, status="FINALIZED"))
