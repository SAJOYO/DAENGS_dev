"""Service factory -> real preparation/writing/receipt; old card policy is never executed."""

import asyncio
import json
from dataclasses import replace
from itertools import pairwise
from unittest.mock import AsyncMock

import pytest

from daengs_backend.services.walk_diary.preparation.board import assemble_saved_base_board
from daengs_backend.services.walk_diary.preparation.relational import prepare_relational_diary
from daengs_backend.services.walk_diary.relational_execution import (
    RelationalConfigurationError,
    RelationalDiaryResult,
    RelationalExecutionPolicy,
)
from daengs_backend.services.walk_diary.runtime import write_relational_board
from daengs_backend.services.walk_diary.writing.relational_transport import (
    CallCoordinator,
    CallsStopped,
    ProviderFailure,
)
from tests.walk.diary.test_diary_space_integration import public_collector  # noqa: F401
from tests.walk.diary.test_relational_takeover import assessment
from tests.walk.support.base_board import policy, saved_case


def execution(**overrides):
    # These historical v7 replay cases explicitly exercise the optional review path.
    return RelationalExecutionPolicy(
        **{"minimum_interval_s": 0, "semantic_review": True, **overrides}
    )


@pytest.fixture
def base():
    source, _, _ = saved_case()
    return assemble_saved_base_board(source, policy(3))


@pytest.fixture
def prepare(public_collector):  # noqa: F811
    async def collect(base, *, scene_ids=None):
        backgrounds = await public_collector(base.board)
        return prepare_relational_diary(
            replace(base, scene_backgrounds=backgrounds), scene_ids=scene_ids
        )

    return collect


async def send(stage, payload, schema):
    if stage == "review":
        return json.dumps(assessment(payload["candidate"]["evidence_ids"]))
    if stage == "title":
        return json.dumps({"title": "산책 기록"})
    if stage == "action":
        return json.dumps(
            {"text": "냄새를 맡았다.", "evidence_ids": [payload["recorded_action"]["id"]]}
        )
    return json.dumps(
        {
            "focus": "현재 공간",
            "text": "길이 있는 곳이었다.",
            "evidence_ids": [payload["current"]["facts"][0]["id"]],
            "relation_ids": [],
        }
    )


async def test_default_service_factory_uses_our_preparation_and_receipt(base, prepare, monkeypatch):
    from daengs_backend.orchestration import diary
    from daengs_backend.services.walk_diary.collection import relational
    from daengs_backend.services.walk_diary.writing import assembly

    def forbidden(*args, **kwargs):
        raise AssertionError("old graph and card assembly must not run")

    monkeypatch.setattr(diary, "DiaryOrchestrationService", forbidden)
    monkeypatch.setattr(assembly, "complete_cards", forbidden)
    from daengs_backend.services.walk_diary.preparation import scene_snapshot

    validate_sources = scene_snapshot.validate_scene_snapshot_bindings
    source_checks = []

    def counted_sources(snapshot):
        source_checks.append(len(snapshot["frames"]))
        return validate_sources(snapshot)

    monkeypatch.setattr(scene_snapshot, "validate_scene_snapshot_bindings", counted_sources)
    collector = AsyncMock(side_effect=prepare)
    monkeypatch.setattr(relational, "configured_relational_preparation", collector)
    seen = []

    async def model(stage, payload, schema):
        seen.append((stage, payload))
        return await send(stage, payload, schema)

    result = await write_relational_board(
        base.input.source, base, send=model, execution_policy=execution()
    )
    assert isinstance(result, RelationalDiaryResult)
    assert result.receipt["version"] == "relational-diary-skeleton-v7"
    assert result.input_revision == base.input.source.revision()
    assert all(t["status"] == "returned" for t in result.receipt["scene_titles"].values())
    assert seen[-1][0] == "title"
    assert all(r["status"] == "returned" for r in result.receipt["writing"]["results"])
    assert len(seen) == result.receipt["execution"]["model_call_attempts"]
    assert (
        result.receipt["execution"]["policy"]["generation_timeout_s"]
        == result.receipt["execution"]["total_timeout_s"]
    )
    assert result.receipt["execution"]["total_timeout_s"] >= 180
    assert "originals" not in json.dumps(seen) and "card_header" not in json.dumps(seen)
    collector.assert_awaited_once()
    assert source_checks == [len(result.prepared["snapshot"]["frames"])]


async def test_source_mismatch_fails_before_acquisition(base):
    collector = AsyncMock()
    changed = base.input.source.model_copy(update={"owner_id": "different-owner"})
    with pytest.raises(ValueError, match="prepared source"):
        await write_relational_board(changed, base, prepare=collector, send=send)
    collector.assert_not_awaited()


async def test_missing_config_stops_before_collection_and_pacing(base, monkeypatch):
    from pydantic import SecretStr

    from daengs_backend.config import settings

    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(""))
    collector = AsyncMock()
    with pytest.raises(RelationalConfigurationError):
        await write_relational_board(base.input.source, base, prepare=collector)
    collector.assert_not_awaited()


async def test_wrong_preparation_or_missing_plan_never_calls_writer(base, prepare):
    from daengs_walk.value_contracts import digest

    async def wrong(base, *, scene_ids):
        result = await prepare(base, scene_ids=scene_ids)
        result["snapshot"]["plans"].pop()
        result["revision"] = digest(result["snapshot"])
        return result

    model = AsyncMock()
    with pytest.raises(ValueError, match="different relational preparation"):
        await write_relational_board(
            base.input.source, base, prepare=wrong, send=model, execution_policy=execution()
        )
    model.assert_not_awaited()


async def test_partial_failure_remains_empty_and_no_old_fallback(base, prepare):
    async def failure(stage, payload, schema):
        if stage == "space":
            raise ProviderFailure(503)
        return await send(stage, payload, schema)

    result = await write_relational_board(
        base.input.source,
        base,
        prepare=prepare,
        send=failure,
        execution_policy=execution(semantic_review=False),
    )
    assert all(c["parts"]["space"]["text"] == "" for c in result.receipt["cards"])
    assert all(c["parts"]["space"]["status"] == "failed" for c in result.receipt["cards"])
    assert all(c["comparison"]["context"]["current"]["facts"] for c in result.receipt["cards"])


async def test_429_stops_writing_review_and_title_without_retry(base, prepare):
    model = AsyncMock(side_effect=ProviderFailure(429))
    result = await write_relational_board(
        base.input.source, base, prepare=prepare, send=model, execution_policy=execution()
    )
    assert model.await_count == 1
    assert result.receipt["execution"]["stopped_on_rate_limit"]
    assert result.receipt["execution"]["automatic_retries"] == 0


async def test_preparation_timeout_does_not_start_model(base):
    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()

    model = AsyncMock()
    with pytest.raises(TimeoutError):
        await write_relational_board(
            base.input.source,
            base,
            prepare=blocked,
            send=model,
            execution_policy=execution(preparation_timeout_s=0.01),
        )
    model.assert_not_awaited()


async def test_timeout_keeps_failed_results_without_late_adoption(base, prepare):
    cancelled = []

    async def blocked(stage, payload, schema):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(stage)

    result = await write_relational_board(
        base.input.source,
        base,
        prepare=prepare,
        send=blocked,
        execution_policy=execution(generation_timeout_s=0.03, call_timeout_s=0.02),
    )
    assert len(cancelled) == result.receipt["execution"]["model_call_attempts"]
    assert result.receipt["execution"]["stopped_on_deadline"]
    assert all(c["parts"]["space"]["text"] == "" for c in result.receipt["cards"])


async def test_deadline_never_shortens_completion_interval():
    now = [0.0]
    calls = []

    async def model(*args):
        calls.append(now[0])
        now[0] += 2
        return "response"

    coordinator = CallCoordinator(
        model, minimum_interval_s=10, total_timeout_s=8, clock=lambda: now[0]
    )
    await coordinator("space", {}, {})
    with pytest.raises(CallsStopped):
        await coordinator("action", {}, {})
    assert calls == [0.0] and coordinator.deadline_reached


async def test_per_call_timeout_cancels_the_sender():
    cancelled = []

    async def blocked(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    coordinator = CallCoordinator(blocked, call_timeout_s=0.01)
    with pytest.raises(TimeoutError):
        await coordinator("space", {}, {})
    assert cancelled == [True]
    assert coordinator.trace[0]["status"] == "failed"


async def test_provider_cannot_publish_after_suppressing_timeout():
    async def late(*args):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return "late response"

    coordinator = CallCoordinator(late, call_timeout_s=0.01)
    with pytest.raises(TimeoutError):
        await coordinator("space", {}, {})
    assert coordinator.trace[0]["status"] == "failed"


async def test_current_action_goes_through_same_service(prepare):
    from tests.walk.diary.test_diary_activity import prepared

    base, _ = prepared()
    seen, reviewed = [], []

    async def model(stage, payload, schema):
        if stage == "action":
            seen.append(payload)
        if stage == "review" and payload["part"] == "action":
            reviewed.append(payload["evidence"])
        return await send(stage, payload, schema)

    result = await write_relational_board(
        base.input.source, base, prepare=prepare, send=model, execution_policy=execution()
    )
    assert seen and seen[0]["current_gait"]
    assert "earlier" not in seen[0] and "relation_slots" not in seen[0]
    assert any(c["parts"]["action"]["status"] == "returned" for c in result.receipt["cards"])

    assert len(reviewed) == len(seen)
    frames = {f["anchor"]["event_at"]: f for f in result.prepared["snapshot"]["frames"]}
    for request in seen:
        assert request["current_space"]
        facts = {
            "space:" + fact["id"]: fact
            for fact in frames[request["pin_at"]]["scene_snapshot"]["facts"]
            if fact["family"] == "land_cover"
        }
        for background in request["current_space"]:
            source_meaning = facts[background["id"]]["time_meaning"]
            assert source_meaning
            assert background["time_meaning"] == source_meaning
        review_input = next(r for r in reviewed if r["pin_at"] == request["pin_at"])
        assert review_input == request


@pytest.mark.parametrize(
    "review,scenes,actions,calls,seconds",
    [
        (True, 3, 1, 11, 280),
        (True, 12, 0, 36, 905),
        (False, 12, 0, 24, 605),
        (True, 12, 12, 60, 1505),
    ],
)
def test_auto_budget_includes_writing_review_title_and_completion_gaps(
    review, scenes, actions, calls, seconds
):
    policy = RelationalExecutionPolicy(semantic_review=review)
    resolved = policy.resolve(scenes, actions)
    assert resolved.call_budget(scenes, actions) == calls
    assert resolved.generation_timeout_s == seconds
    assert resolved.minimum_interval_s == 10
    assert policy.generation_timeout_s is None


def test_explicit_deadline_and_call_cap_are_preserved():
    policy = RelationalExecutionPolicy(generation_timeout_s=7, max_calls=4)
    assert policy.resolve(12, 12) is policy
    assert policy.call_budget(12, 12) == 4
    assert RelationalExecutionPolicy(max_calls=4).resolve(12, 12).generation_timeout_s == 180


async def test_twelve_scenes_can_finish_with_pacing_and_review_without_real_waiting():
    policy = RelationalExecutionPolicy(semantic_review=True).resolve(12, 0)
    now, started = [0.0], []

    async def sleep(seconds):
        now[0] += seconds

    async def model(*args):
        started.append(now[0])
        now[0] += 14  # Within each 15-second allowance.
        return "{}"

    count = policy.call_budget(12, 0)
    coordinator = CallCoordinator(
        model,
        clock=lambda: now[0],
        sleep=sleep,
        minimum_interval_s=policy.minimum_interval_s,
        max_calls=count,
        call_timeout_s=policy.call_timeout_s,
        total_timeout_s=policy.generation_timeout_s,
    )
    for stage in ["space", "review"] * 12 + ["title"] * 12:
        await coordinator(stage, {}, {})
    assert len(started) == 36 and not coordinator.deadline_reached
    assert all(b - a >= 24 for a, b in pairwise(started))
    assert now[0] < policy.generation_timeout_s


@pytest.mark.parametrize("total_limit", [False, True])
async def test_expired_timeout_rejects_swallowed_cancellation_even_with_frozen_clock(total_limit):
    async def swallow(*args):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return "must not publish"

    coordinator = CallCoordinator(
        swallow,
        clock=lambda: 0.0,
        call_timeout_s=1.0 if total_limit else 0.001,
        total_timeout_s=0.001 if total_limit else None,
    )
    with pytest.raises(TimeoutError):
        await coordinator("space", {}, {})
    assert coordinator.trace[0]["status"] == "failed"
    assert coordinator.deadline_reached == total_limit
    if total_limit:
        with pytest.raises(CallsStopped):
            await coordinator("action", {}, {})
        assert coordinator.calls == 1
