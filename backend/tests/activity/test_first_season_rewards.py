"""Agreed first-season examples and the portable DEV reward boundary."""

import json
from dataclasses import asdict, replace

import pytest

from daengs_backend.services.activity_core.first_season_rewards import (
    REWARD_VERSION,
    BaseEntitlement,
    BaseRewardKey,
    HoldingScore,
    RewardEvent,
    RewardRules,
    plan_reward,
    settle_holding,
)
from daengs_backend.services.activity_core.game_policy import (
    HOUR_MS,
    POINT_DENOMINATOR,
    GameError,
    Rules,
)

KEY = BaseRewardKey("september", "member-a", "pole-a")


def test_cli_requires_complete_new_snapshot_and_score_response_keeps_breakdown(tmp_path):
    from daengs_backend.cli.activity import parse_rules
    from daengs_backend.schemas.activity import ScoreResponse
    from daengs_backend.services.activity_core.first_season_policy import Rules as SeasonRules
    from daengs_backend.services.activity_core.first_season_policy import Score

    path = tmp_path / "rules.json"
    path.write_text(json.dumps(asdict(SeasonRules())), encoding="utf-8")
    assert parse_rules(path) == SeasonRules()
    path.write_text(json.dumps(asdict(Rules())), encoding="utf-8")
    assert parse_rules(path) == Rules()
    path.write_text(json.dumps({"version": REWARD_VERSION}), encoding="utf-8")
    with pytest.raises(ValueError, match="explicitly specify every"):
        parse_rules(path)
    score = Score(bonus=120, base_bonus=100, takeover_bonus=20)
    response = ScoreResponse(**asdict(score))
    assert (response.bonus, response.base_bonus, response.takeover_bonus) == (120, 100, 20)


def event(**overrides):
    values = {
        "key": KEY,
        "event_id": "photo:1",
        "pet_id": "dog-a",
        "certification": "VERIFIED",
        "kind": "ACQUIRED",
    }
    return RewardEvent(**(values | overrides))


def test_unverified_acquire_upgrade_then_renew():
    first = plan_reward(event(event_id="mark:1", certification="UNVERIFIED"), BaseEntitlement(KEY))
    upgrade = plan_reward(
        event(
            kind="CERTIFIED",
            previous_member_id=KEY.member_id,
            previous_certification="UNVERIFIED",
        ),
        first.entitlement,
    )
    renewal = plan_reward(
        event(
            event_id="photo:2",
            kind="RENEWED",
            previous_member_id=KEY.member_id,
            previous_certification="VERIFIED",
        ),
        upgrade.entitlement,
    )
    assert [p.credit_points for p in (first, upgrade, renewal)] == [20, 80, 0]
    assert [p.receipt.takeover_points for p in (first, upgrade, renewal)] == [0, 0, 0]
    assert renewal.entitlement.paid == 100


@pytest.mark.parametrize("previous_certification", ["UNVERIFIED", "VERIFIED"])
@pytest.mark.parametrize("paid,base,total", [(0, 100, 120), (20, 80, 100), (100, 0, 20)])
def test_takeover_reward_matrix(previous_certification, paid, base, total):
    result = plan_reward(
        event(
            previous_member_id="member-b",
            previous_certification=previous_certification,
        ),
        BaseEntitlement(KEY, paid),
    )
    assert result.receipt.base_points == base
    assert result.receipt.takeover_points == 20
    assert result.credit_points == total
    assert result.entitlement.paid == 100


def test_member_entitlement_is_shared_but_each_dog_keeps_its_credit():
    first = plan_reward(event(event_id="mark:1", certification="UNVERIFIED"), BaseEntitlement(KEY))
    second = plan_reward(
        event(
            pet_id="dog-b",
            previous_member_id=KEY.member_id,
            previous_certification="UNVERIFIED",
        ),
        first.entitlement,
    )
    third = plan_reward(
        event(
            pet_id="dog-c",
            event_id="photo:2",
            previous_member_id=KEY.member_id,
            previous_certification="VERIFIED",
        ),
        second.entitlement,
    )
    assert [(p.receipt.event.pet_id, p.credit_points) for p in (first, second, third)] == [
        ("dog-a", 20),
        ("dog-b", 80),
        ("dog-c", 0),
    ]
    assert all(p.receipt.takeover_points == 0 for p in (first, second, third))


@pytest.mark.parametrize(
    "paid,certification,total",
    [
        (0, "UNVERIFIED", 20),
        (20, "UNVERIFIED", 0),
        (100, "UNVERIFIED", 0),
        (0, "VERIFIED", 100),
        (20, "VERIFIED", 80),
        (100, "VERIFIED", 0),
    ],
)
def test_vacant_or_expired_site_uses_member_history(paid, certification, total):
    result = plan_reward(event(certification=certification), BaseEntitlement(KEY, paid))
    assert result.credit_points == total
    assert result.receipt.takeover_points == 0
    assert result.entitlement.paid >= paid


def test_separate_successful_takeovers_repeat_but_same_event_replay_never_credits():
    # These are already admitted decisions, each after photo/protection validation.
    ledgers = {member: BaseEntitlement(replace(KEY, member_id=member)) for member in ("a", "b")}
    totals = {"a": 0, "b": 0}
    previous = None
    for index in range(20):
        member = "a" if index % 2 == 0 else "b"
        decision = event(
            key=ledgers[member].key,
            event_id=f"photo:{index}",
            pet_id=f"dog-{member}",
            previous_member_id=previous,
            previous_certification="VERIFIED" if previous else None,
        )
        result = plan_reward(decision, ledgers[member])
        totals[member] += result.credit_points
        ledgers[member] = result.entitlement
        replay = plan_reward(decision, ledgers[member], existing_receipt=result.receipt)
        assert replay.replayed and replay.credit_points == 0
        assert replay.receipt == result.receipt
        previous = member
    assert totals == {"a": 100 + 9 * 20, "b": 100 + 10 * 20}
    assert all(ledger.paid == 100 for ledger in ledgers.values())


def test_old_receipt_replay_does_not_restore_pre_upgrade_entitlement():
    decision = event(event_id="mark:1", certification="UNVERIFIED")
    first = plan_reward(decision, BaseEntitlement(KEY))
    upgraded = plan_reward(
        event(
            kind="CERTIFIED", previous_member_id=KEY.member_id, previous_certification="UNVERIFIED"
        ),
        first.entitlement,
    )
    replay = plan_reward(decision, upgraded.entitlement, existing_receipt=first.receipt)
    assert replay.credit_points == 0
    assert replay.receipt.total_points == 20
    assert replay.entitlement.paid == 100


@pytest.mark.parametrize(
    "changes",
    [
        {"pet_id": "dog-other"},
        {"event_id": "photo:different"},
        {"key": replace(KEY, member_id="other")},
    ],
)
def test_receipt_cannot_be_reused_for_a_different_identity(changes):
    first = plan_reward(event(), BaseEntitlement(KEY))
    changed = event(**changes)
    with pytest.raises(GameError, match="event_identity_conflict"):
        plan_reward(changed, BaseEntitlement(changed.key, 100), existing_receipt=first.receipt)


def test_receipt_corruption_or_missing_ledger_fails_closed():
    first = plan_reward(event(), BaseEntitlement(KEY))
    with pytest.raises(GameError, match="invalid_reward_receipt"):
        plan_reward(
            event(), first.entitlement, existing_receipt=replace(first.receipt, takeover_points=20)
        )
    with pytest.raises(GameError, match="base_reward_state_missing"):
        plan_reward(event(), BaseEntitlement(KEY), existing_receipt=first.receipt)


@pytest.mark.parametrize("field", ["season_id", "member_id", "site_id"])
def test_entitlement_scope_cannot_be_mixed(field):
    other = replace(KEY, **{field: "different"})
    with pytest.raises(GameError, match="base_reward_scope_mismatch"):
        plan_reward(event(), BaseEntitlement(other, 100))
    assert plan_reward(event(key=other), BaseEntitlement(other)).credit_points == 100


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"certification": "bad"}, "invalid_certification"),
        ({"previous_member_id": "b"}, "invalid_previous_owner"),
        ({"previous_certification": "VERIFIED"}, "invalid_previous_owner"),
        ({"kind": "RENEWED"}, "invalid_reward_transition"),
        (
            {
                "kind": "CERTIFIED",
                "previous_member_id": "b",
                "previous_certification": "UNVERIFIED",
            },
            "invalid_reward_transition",
        ),
        (
            {
                "kind": "CERTIFIED",
                "previous_member_id": KEY.member_id,
                "previous_certification": "VERIFIED",
            },
            "invalid_reward_transition",
        ),
        (
            {
                "certification": "UNVERIFIED",
                "previous_member_id": "b",
                "previous_certification": "UNVERIFIED",
            },
            "photo_required",
        ),
    ],
)
def test_invalid_decisions_are_rejected(changes, code):
    with pytest.raises(GameError, match=code):
        event(**changes)


@pytest.mark.parametrize("paid", [-1, 1, 80, 101, True, 20.0])
def test_invalid_base_history_is_not_silently_repaired(paid):
    with pytest.raises(GameError, match="invalid_base_paid"):
        BaseEntitlement(KEY, paid)


def test_fixed_reward_snapshot_and_legacy_version_are_separate():
    assert RewardRules(**asdict(RewardRules())) == RewardRules()
    assert Rules().version == "certified-protection-v2"
    with pytest.raises(GameError, match="unsupported_policy_version"):
        Rules(version=REWARD_VERSION)
    with pytest.raises(GameError, match="unsupported_reward_version"):
        RewardRules(version="certified-protection-v2")
    with pytest.raises(GameError, match="invalid_first_season_balance"):
        RewardRules(takeover_bonus=100)


def test_holding_mixed_counts_is_linear_without_coefficient():
    mixed = settle_holding(HoldingScore(unverified_count=3, verified_count=2), HOUR_MS)
    assert mixed.whole_points == 26
    assert mixed.held_site_ms == 5 * HOUR_MS
    many = settle_holding(HoldingScore(unverified_count=30, verified_count=20), HOUR_MS)
    assert many.holding_units == mixed.holding_units * 10


def test_upgrade_settles_old_rate_before_changing_counts():
    before = settle_holding(HoldingScore(unverified_count=1), HOUR_MS)
    upgraded = replace(before, unverified_count=0, verified_count=1)
    after = settle_holding(upgraded, 2 * HOUR_MS)
    assert before.whole_points == 2
    assert after.whole_points == 12
    assert after.held_site_ms == 2 * HOUR_MS


def test_loss_stops_holding_and_reacquisition_does_not_fill_the_gap():
    owned = settle_holding(HoldingScore(verified_count=1), HOUR_MS)
    empty = settle_holding(replace(owned, verified_count=0), 5 * HOUR_MS)
    regained = settle_holding(replace(empty, verified_count=1), 6 * HOUR_MS)
    assert regained.whole_points == 20
    assert regained.held_site_ms == 2 * HOUR_MS


def test_fractional_intervals_preserve_precision_and_settlement_is_idempotent():
    start = HoldingScore(unverified_count=1, verified_count=1)
    split = settle_holding(settle_holding(start, 1), HOUR_MS)
    assert split == settle_holding(start, HOUR_MS)
    assert split.holding_units == 12 * POINT_DENOMINATOR
    assert settle_holding(split, HOUR_MS) == split


def test_valid_72_hour_interval_has_no_four_hour_cap():
    assert settle_holding(HoldingScore(verified_count=1), 72 * HOUR_MS).whole_points == 720
    assert settle_holding(HoldingScore(unverified_count=1), 72 * HOUR_MS).whole_points == 144


@pytest.mark.parametrize("at_ms", [-1, 4, True, 6.0])
def test_holding_rejects_reversed_or_noninteger_time(at_ms):
    with pytest.raises(GameError, match="time_reversed"):
        settle_holding(HoldingScore(last_ms=5), at_ms)


@pytest.mark.parametrize(
    "changes",
    [
        {"verified_count": -1},
        {"unverified_count": True},
        {"holding_units": -1},
        {"held_site_ms": 0.5},
        {"last_ms": -1},
    ],
)
def test_holding_rejects_invalid_persisted_balances(changes):
    with pytest.raises(GameError, match="invalid_holding_score"):
        HoldingScore(**changes)
