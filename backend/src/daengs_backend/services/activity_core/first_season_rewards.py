"""First-season reward arithmetic, independent of ownership admission and storage.

Events come from a trusted, successful ownership decision, never an HTTP body.
The adapter must lock the member entitlement and persist ownership, pet credits,
entitlement and the unique (season, event) receipt in ONE transaction. This module
does not enable the policy in the legacy runtime or enforce photo/protection rules.
"""

from dataclasses import dataclass, replace
from typing import Literal

from daengs_backend.services.activity_core.game_policy import (
    POINT_DENOMINATOR,
    Certification,
    require,
)

REWARD_VERSION = "first-season-rewards-v1"


def _identity(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _nonnegative(value: int) -> bool:
    return type(value) is int and value >= 0


@dataclass(frozen=True)
class RewardRules:
    """Immutable snapshot. Changing these agreed values requires another version."""

    version: str = REWARD_VERSION
    unverified_base_target: int = 20
    verified_base_target: int = 100
    takeover_bonus: int = 20
    unverified_hourly_points: int = 2
    verified_hourly_points: int = 10

    def __post_init__(self):
        require(self.version == REWARD_VERSION, "unsupported_reward_version")
        values = (
            self.unverified_base_target,
            self.verified_base_target,
            self.takeover_bonus,
            self.unverified_hourly_points,
            self.verified_hourly_points,
        )
        require(
            all(type(value) is int for value in values) and values == (20, 100, 20, 2, 10),
            "invalid_first_season_balance",
        )


DEFAULT_REWARD_RULES = RewardRules()


@dataclass(frozen=True)
class BaseRewardKey:
    season_id: str
    member_id: str
    site_id: str

    def __post_init__(self):
        require(
            all(_identity(value) for value in (self.season_id, self.member_id, self.site_id)),
            "empty_reward_identity",
        )


@dataclass(frozen=True)
class BaseEntitlement:
    key: BaseRewardKey
    paid: int = 0

    def __post_init__(self):
        require(type(self.paid) is int and self.paid in (0, 20, 100), "invalid_base_paid")


@dataclass(frozen=True)
class RewardEvent:
    """Historical decision with server-resolved member identities.

    ACQUIRED includes a new dog taking ownership, even within the same member.
    CERTIFIED upgrades existing unverified ownership; RENEWED only maintains it.
    An expired site is vacant (both previous fields None), regardless of history.
    event_id is the stable mark:<claim UUID> / photo:<photo UUID> source-stage key.
    """

    key: BaseRewardKey
    event_id: str
    pet_id: str
    certification: Certification
    kind: Literal["ACQUIRED", "CERTIFIED", "RENEWED"]
    previous_member_id: str | None = None
    previous_certification: Certification | None = None

    def __post_init__(self):
        require(_identity(self.event_id) and _identity(self.pet_id), "empty_reward_identity")
        require(self.certification in ("UNVERIFIED", "VERIFIED"), "invalid_certification")
        require(self.kind in ("ACQUIRED", "CERTIFIED", "RENEWED"), "invalid_reward_kind")
        if self.previous_member_id is None:
            require(self.previous_certification is None, "invalid_previous_owner")
            require(self.kind == "ACQUIRED", "invalid_reward_transition")
        else:
            require(
                _identity(self.previous_member_id)
                and self.previous_certification in ("UNVERIFIED", "VERIFIED"),
                "invalid_previous_owner",
            )
            if self.kind == "ACQUIRED":
                require(self.certification == "VERIFIED", "photo_required")
            else:
                require(
                    self.previous_member_id == self.key.member_id,
                    "invalid_reward_transition",
                )
                require(
                    (
                        self.kind == "CERTIFIED"
                        and self.previous_certification == "UNVERIFIED"
                        and self.certification == "VERIFIED"
                    )
                    or (
                        self.kind == "RENEWED" and self.certification == self.previous_certification
                    ),
                    "invalid_reward_transition",
                )


@dataclass(frozen=True)
class RewardReceipt:
    event: RewardEvent
    rules: RewardRules
    base_before: BaseEntitlement
    base_after: BaseEntitlement
    base_points: int
    takeover_points: int

    @property
    def total_points(self) -> int:
        return self.base_points + self.takeover_points


@dataclass(frozen=True)
class RewardPlan:
    receipt: RewardReceipt
    entitlement: BaseEntitlement
    replayed: bool = False

    @property
    def credit_points(self) -> int:
        """Credit receipt.event.pet_id only; a historical replay creates no credit."""
        return 0 if self.replayed else self.receipt.total_points


def plan_reward(
    event: RewardEvent,
    entitlement: BaseEntitlement,
    *,
    rules: RewardRules = DEFAULT_REWARD_RULES,
    existing_receipt: RewardReceipt | None = None,
) -> RewardPlan:
    """Calculate a delta without mutation. Storage uniqueness/locking is mandatory.

    On retry, the adapter reads the receipt BEFORE re-evaluating current ownership.
    Reuse its historical event only after verifying the immutable source request.
    Pass the latest locked entitlement so replay never restores an older balance.
    """
    require(event.key == entitlement.key, "base_reward_scope_mismatch")
    if existing_receipt is not None:
        require(existing_receipt.event == event, "event_identity_conflict")
        require(existing_receipt.rules == rules, "reward_rules_mismatch")
        expected = plan_reward(event, existing_receipt.base_before, rules=rules).receipt
        require(existing_receipt == expected, "invalid_reward_receipt")
        require(entitlement.paid >= existing_receipt.base_after.paid, "base_reward_state_missing")
        return RewardPlan(existing_receipt, entitlement, replayed=True)

    target = (
        rules.verified_base_target
        if event.certification == "VERIFIED"
        else rules.unverified_base_target
    )
    base = 0 if event.kind == "RENEWED" else max(0, target - entitlement.paid)
    takeover = (
        rules.takeover_bonus
        if event.kind == "ACQUIRED"
        and event.previous_member_id is not None
        and event.previous_member_id != event.key.member_id
        else 0
    )
    after = replace(entitlement, paid=entitlement.paid + base)
    return RewardPlan(RewardReceipt(event, rules, entitlement, after, base, takeover), after)


@dataclass(frozen=True)
class HoldingScore:
    """A pet's constant-count interval balance; units match legacy point precision."""

    unverified_count: int = 0
    verified_count: int = 0
    holding_units: int = 0
    held_site_ms: int = 0
    last_ms: int = 0

    def __post_init__(self):
        require(
            all(
                _nonnegative(value)
                for value in (
                    self.unverified_count,
                    self.verified_count,
                    self.holding_units,
                    self.held_site_ms,
                    self.last_ms,
                )
            ),
            "invalid_holding_score",
        )

    @property
    def whole_points(self) -> int:
        """Presentation only; persist holding_units without rounding each interval."""
        return self.holding_units // POINT_DENOMINATOR


def settle_holding(
    score: HoldingScore, at_ms: int, *, rules: RewardRules = DEFAULT_REWARD_RULES
) -> HoldingScore:
    """Settle BEFORE changing counts/certification, with no simultaneous-site factor.

    Caller splits at ownership changes and effective expiry/season boundaries. An
    aggregate count has no per-site expiry information; never pass a delayed worker's
    wall clock across such a boundary. Expiry scheduling belongs to the next stage.
    """
    require(_nonnegative(at_ms) and at_ms >= score.last_ms, "time_reversed")
    elapsed = at_ms - score.last_ms
    hourly = (
        score.unverified_count * rules.unverified_hourly_points
        + score.verified_count * rules.verified_hourly_points
    )
    return replace(
        score,
        holding_units=score.holding_units + elapsed * hourly * 10_000,
        held_site_ms=score.held_site_ms + elapsed * (score.unverified_count + score.verified_count),
        last_ms=at_ms,
    )
