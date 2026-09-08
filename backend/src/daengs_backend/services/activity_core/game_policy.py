"""Portable policy boundary for Geo and an authenticated DEV ownership adapter.

No HTTP, clock, database, synthetic dog, session store or Game snapshot dependency.
All values are immutable. Ownership candidates must originate in the trusted claim
service after authorization and contact/photo validation, never directly from JSON.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
POINT_DENOMINATOR = HOUR_MS * 10_000
CONTRACT_VERSION = "territory-policy.v1"
Certification = Literal["UNVERIFIED", "VERIFIED"]


class GameError(ValueError):
    """Stable conflict code, also used by the local HTTP adapter."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise GameError(code)


@dataclass(frozen=True)
class Rules:
    version: str = "draft-2026-09-06"
    protection_ms: int = 600_000
    claim_points: int = 100
    takeover_points: int = 100
    hourly_points: int = 10
    extra_site_bps: int = 1000
    maximum_bps: int = 20_000
    repeat_bonus: str = "every_change"
    unverified_scores: bool = True

    def __post_init__(self):
        require(self.protection_ms == 600_000, "protection_must_be_ten_minutes")
        for value in (
            self.claim_points,
            self.takeover_points,
            self.hourly_points,
            self.extra_site_bps,
            self.maximum_bps,
        ):
            require(type(value) is int and 0 <= value <= 1_000_000, "invalid_balance")
        require(self.maximum_bps >= 10_000, "invalid_multiplier_cap")
        require(self.repeat_bonus in {"every_change", "daily_pet_site"}, "invalid_repeat_bonus")
        require(type(self.unverified_scores) is bool, "invalid_unverified_policy")

    def multiplier(self, count: int) -> int:
        return min(10_000 + max(0, count - 1) * self.extra_site_bps, self.maximum_bps)


@dataclass(frozen=True)
class Score:
    bonus: int = 0
    holding_units: int = 0
    held_site_ms: int = 0
    current_count: int = 0
    scoring_count: int = 0
    peak: int = 0
    claims: int = 0
    takeovers: int = 0
    last_ms: int = 0


@dataclass(frozen=True)
class SeasonContext:
    season_id: str
    starts_ms: int
    ends_ms: int
    rules: Rules
    status: Literal["ACTIVE", "FINALIZED"] = "ACTIVE"


@dataclass(frozen=True)
class Ownership:
    pet_id: str
    session_id: str
    attempt_id: str
    certification: Certification
    occupied_ms: int


@dataclass(frozen=True)
class SiteSnapshot:
    season_id: str
    site_id: str
    version: int
    owner: Ownership | None


@dataclass(frozen=True)
class OwnershipCandidate:
    """An authenticated claim decision, before persistence; not an app request body.

    event_id is a stable source-stage key: mark:<claim UUID> or photo:<photo UUID>.
    Retrying at a later processing time keeps this exact identity and content.
    """

    season_id: str
    event_id: str
    site_id: str
    expected_version: int
    pet_id: str
    session_id: str
    attempt_id: str
    certification: Certification
    cause: Literal["MARK", "PHOTO_VERIFIED"]

    def __post_init__(self):
        require(
            all(
                value.strip()
                for value in (
                    self.season_id,
                    self.event_id,
                    self.site_id,
                    self.pet_id,
                    self.session_id,
                    self.attempt_id,
                )
            ),
            "empty_policy_identity",
        )
        require(
            type(self.expected_version) is int and self.expected_version >= 0,
            "invalid_site_version",
        )
        require(
            (self.cause, self.certification)
            in {
                ("MARK", "UNVERIFIED"),
                ("PHOTO_VERIFIED", "VERIFIED"),
            },
            "invalid_claim_decision",
        )


@dataclass(frozen=True)
class BonusKey:
    season_id: str
    pet_id: str
    site_id: str
    utc_day: int


@dataclass(frozen=True)
class Account:
    pet_id: str
    score: Score


@dataclass(frozen=True)
class OwnershipPlan:
    candidate: OwnershipCandidate
    at_ms: int
    before: SiteSnapshot
    after: SiteSnapshot
    accounts: tuple[Account, ...]
    bonus: int
    bonus_key: BonusKey | None
    kind: Literal["OWNERSHIP_CHANGED", "CERTIFIED", "UNCHANGED"]


@dataclass(frozen=True)
class Receipt:
    """Historical applied result. Read current site separately; it may have changed."""

    candidate: OwnershipCandidate
    at_ms: int
    site_version: int
    bonus: int
    kind: str
    contract_version: str = CONTRACT_VERSION


def settle(score: Score, at_ms: int, rules: Rules) -> Score:
    require(type(at_ms) is int and at_ms >= score.last_ms, "time_reversed")
    require(0 <= score.scoring_count <= score.current_count <= score.peak, "invalid_score_counts")
    elapsed = at_ms - score.last_ms
    return replace(
        score,
        holding_units=score.holding_units
        + elapsed
        * score.scoring_count
        * rules.hourly_points
        * rules.multiplier(score.scoring_count),
        held_site_ms=score.held_site_ms + elapsed * score.current_count,
        last_ms=at_ms,
    )


def protected_until(owner: Ownership | None, rules: Rules) -> int | None:
    return owner.occupied_ms + rules.protection_ms if owner else None


def is_protected(owner: Ownership | None, pet_id: str, at_ms: int, rules: Rules) -> bool:
    return bool(owner and owner.pet_id != pet_id and at_ms < protected_until(owner, rules))


def bonus_key(candidate: OwnershipCandidate, at_ms: int, rules: Rules) -> BonusKey | None:
    if rules.repeat_bonus != "daily_pet_site":
        return None
    return BonusKey(candidate.season_id, candidate.pet_id, candidate.site_id, at_ms // DAY_MS)


def validate_context(
    season: SeasonContext, site: SiteSnapshot, candidate: OwnershipCandidate, at_ms: int
) -> None:
    require(season.season_id == site.season_id == candidate.season_id, "season_mismatch")
    require(site.site_id == candidate.site_id, "site_mismatch")
    require(
        season.status == "ACTIVE" and season.starts_ms <= at_ms < season.ends_ms, "season_ended"
    )
    require(site.version == candidate.expected_version, "site_changed")
    require(not is_protected(site.owner, candidate.pet_id, at_ms, season.rules), "protected")
    if site.owner:
        # DEV imports existing #260 occupancy. Protection uses its actual acquisition
        # time; score accrual still starts at the season's account.last_ms.
        require(0 <= site.owner.occupied_ms <= at_ms, "invalid_ownership_time")
        if site.owner.pet_id != candidate.pet_id:
            require(candidate.cause == "PHOTO_VERIFIED", "photo_required")
            require(site.owner.session_id != candidate.session_id, "new_session_required")


def plan_ownership(
    season: SeasonContext,
    site: SiteSnapshot,
    candidate: OwnershipCandidate,
    scores: Mapping[str, Score],
    *,
    at_ms: int,
    bonus_already_paid: bool = False,
) -> OwnershipPlan:
    validate_context(season, site, candidate, at_ms)
    old, rules = site.owner, season.rules
    same_pet = old is not None and old.pet_id == candidate.pet_id
    affected = sorted({candidate.pet_id} | ({old.pet_id} if old else set()))
    require(set(scores) == set(affected), "score_state_missing")
    if same_pet and (old.certification == "VERIFIED" or candidate.cause == "MARK"):
        return OwnershipPlan(candidate, at_ms, site, site, (), 0, None, "UNCHANGED")
    accounts = {}
    for pet in affected:
        require(season.starts_ms <= scores[pet].last_ms <= at_ms, "invalid_score_time")
        accounts[pet] = settle(scores[pet], at_ms, rules)
    if old:
        previous = accounts[old.pet_id]
        scores_before = int(rules.unverified_scores or old.certification == "VERIFIED")
        require(
            previous.current_count >= 1 and previous.scoring_count >= scores_before,
            "score_state_inconsistent",
        )
        accounts[old.pet_id] = replace(
            previous,
            current_count=previous.current_count - 1,
            scoring_count=previous.scoring_count - scores_before,
        )
    incoming = accounts[candidate.pet_id]
    count = incoming.current_count + 1
    accounts[candidate.pet_id] = replace(
        incoming,
        current_count=count,
        peak=max(incoming.peak, count),
        scoring_count=incoming.scoring_count
        + int(rules.unverified_scores or candidate.certification == "VERIFIED"),
    )
    key = None if same_pet else bonus_key(candidate, at_ms, rules)
    bonus = 0
    if not same_pet:
        if key is None or not bonus_already_paid:
            bonus = rules.takeover_points if old else rules.claim_points
        else:
            key = None
        incoming = accounts[candidate.pet_id]
        accounts[candidate.pet_id] = replace(
            incoming,
            bonus=incoming.bonus + bonus,
            claims=incoming.claims + 1,
            takeovers=incoming.takeovers + int(old is not None),
        )
    owner = (
        replace(old, certification=candidate.certification)
        if same_pet
        else Ownership(
            candidate.pet_id,
            candidate.session_id,
            candidate.attempt_id,
            candidate.certification,
            at_ms,
        )
    )
    return OwnershipPlan(
        candidate,
        at_ms,
        site,
        replace(site, version=site.version + 1, owner=owner),
        tuple(Account(p, accounts[p]) for p in affected),
        bonus,
        key,
        "CERTIFIED" if same_pet else "OWNERSHIP_CHANGED",
    )


@dataclass(frozen=True)
class FinalStanding:
    pet_id: str
    score: Score
    total_units: int
    rank: int


@dataclass(frozen=True)
class FinalizationPlan:
    season: SeasonContext
    results: tuple[FinalStanding, ...]
    accounts: tuple[Account, ...]


def plan_finalization(
    season: SeasonContext, scores: Mapping[str, Score], *, at_ms: int
) -> FinalizationPlan:
    require(season.status == "ACTIVE", "season_ended")
    require(at_ms >= season.ends_ms, "season_not_ended")
    final = {}
    for pet, score in scores.items():
        require(season.starts_ms <= score.last_ms <= season.ends_ms, "invalid_score_time")
        final[pet] = settle(score, season.ends_ms, season.rules)
    ordered = sorted(
        final,
        key=lambda pet: (
            -(final[pet].bonus * POINT_DENOMINATOR + final[pet].holding_units),
            pet,
        ),
    )
    results = []
    previous, rank = None, 0
    for index, pet in enumerate(ordered):
        score = final[pet]
        total = score.bonus * POINT_DENOMINATOR + score.holding_units
        if previous != total:
            rank = index + 1
        previous = total
        results.append(FinalStanding(pet, score, total, rank))
    return FinalizationPlan(
        replace(season, status="FINALIZED"),
        tuple(results),
        tuple(
            Account(p, replace(s, current_count=0, scoring_count=0))
            for p, s in sorted(final.items())
        ),
    )
