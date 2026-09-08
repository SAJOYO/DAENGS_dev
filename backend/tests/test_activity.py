"""DEV-specific adapter contracts, including migration-disabled behavior."""

import uuid
from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from daengs_backend.config import settings
from daengs_backend.schemas.activity import WalkSummaryResponse
from daengs_backend.services import activity, activity_game
from daengs_backend.services.activity_core import game_policy as policy
from daengs_backend.services.activity_core.walk import WalkProjection, summarize_walks


async def test_disabled_hooks_never_touch_new_tables(monkeypatch):
    monkeypatch.setattr(settings, "activity_game_enabled", False)
    db = AsyncMock()
    await activity_game.acquire(db)
    await activity.record_walk(db, object())
    await activity.record_game(db, object())
    await activity.remove_owner(db, uuid.uuid4())
    with pytest.raises(activity.ActivityDisabled):
        await activity.process_pending(db)
    with pytest.raises(activity.ActivityDisabled):
        await activity.walk_summary(db, uuid.uuid4(), 0, 1000)
    assert not db.mock_calls


def test_imported_ownership_keeps_protection_without_backdated_points():
    rules = policy.Rules(version="draft-2026-09-06")
    season = policy.SeasonContext("dev", 100000, 1000000, rules)
    owner = policy.Ownership("a", "old-walk", "old-claim", "UNVERIFIED", 0)
    site = policy.SiteSnapshot("dev", "site", 1, owner)
    candidate = policy.OwnershipCandidate(
        "dev", "event", "site", 1, "b", "new-walk", "new-claim", "VERIFIED", "PHOTO_VERIFIED"
    )
    scores = {
        "a": policy.Score(last_ms=100000, current_count=1, scoring_count=1, peak=1),
        "b": policy.Score(last_ms=100000),
    }
    with pytest.raises(policy.GameError, match="protected"):
        policy.plan_ownership(season, site, candidate, scores, at_ms=599999)
    plan = policy.plan_ownership(season, site, candidate, scores, at_ms=600000)
    assert plan.bonus == 100
    assert plan.accounts[0].score.held_site_ms == 500000


def test_empty_summary_contract_retains_unknown_measurements():
    result = summarize_walks(
        WalkProjection(activity.IDENTITY, activity.VERSIONS, (), ()),
        owner_id=str(uuid.uuid4()),
        from_ms=0,
        to_ms=1000,
    )
    response = WalkSummaryResponse(**asdict(result), status="READY", pending_walk_count=0)
    assert response.observed_walk_count == 0
    assert response.moving_distance_m is None and response.avg_speed_mps is None
