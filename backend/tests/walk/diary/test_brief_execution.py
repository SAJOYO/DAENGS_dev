"""Service -> actual preparation/brief/SDK boundary -> adopted-body title, no external calls."""

import json
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from daengs_backend.services.walk_diary.preparation.relational import prepare_relational_diary
from daengs_backend.services.walk_diary.relational_execution import RelationalExecutionPolicy
from daengs_backend.services.walk_diary.runtime import write_relational_board
from daengs_backend.services.walk_diary.writing.relational import writing_prompt
from daengs_backend.services.walk_diary.writing.relational_transport import ProviderFailure
from daengs_walk.diary.relational.brief_response import brief_response_schema, parse_brief
from daengs_walk.diary.relational.writing_brief import brief_writer_view
from daengs_walk.value_contracts import digest
from tests.walk.diary.test_diary_activity import prepared as activity
from tests.walk.diary.test_diary_space_integration import public_collector  # noqa: F401


@pytest.fixture
def base():
    return activity()[0]


@pytest.fixture
def prepare(public_collector):  # noqa: F811
    async def collect(base, *, scene_ids=None):
        backgrounds = await public_collector(base.board)
        points = {
            digest(s.anchor.point): s.anchor.point.model_dump(mode="json")
            for s in base.board.scenes
            if s.anchor.point
        }
        roads = [
            {
                "point": point,
                "addr_type": 10,
                "response": {"errCd": 0, "result": [{"road_nm": f"산책로{i}길"}]},
            }
            for i, point in enumerate(points.values(), 1)
        ]
        return prepare_relational_diary(
            replace(base, scene_backgrounds=backgrounds),
            scene_ids=scene_ids,
            writing_briefs=True,
            road_snapshots=roads,
        )

    return collect


def answer(stage, payload):
    if stage == "title":
        return json.dumps({"title": "길에서 남긴 산책"})
    if stage == "review":
        raise AssertionError("the default path must not call a reviewer")
    if stage == "action":
        return json.dumps(
            {"text": "반려견이 냄새를 맡았다.", "evidence_ids": [payload["required_event"]["id"]]}
        )
    scene_id = payload["current"]["position"]["scene_id"]
    current = [f for f in payload["available_facts"] if f["scene_id"] == scene_id]
    return json.dumps(
        {
            "focus": "현재 공간과의 관계",
            "text": "공원 가까운 길이었다.",
            "evidence_ids": [current[0]["id"]],
            "relation_ids": [],
        }
    )


async def test_runtime_uses_canonical_briefs_and_one_title_without_review(
    base, prepare, monkeypatch
):
    from daengs_backend.services.walk_diary.collection import relational as collection
    from daengs_backend.services.walk_diary.preparation import scene_snapshot
    from daengs_walk.diary.relational import comparison_planning

    def old_plan(*args, **kwargs):
        raise AssertionError("legacy comparison planning must not enter brief execution")

    monkeypatch.setattr(comparison_planning, "make_comparison_plan", old_plan)
    monkeypatch.setattr(collection, "configured_relational_preparation", prepare)
    checked = []
    original = scene_snapshot.validate_scene_snapshot_bindings

    def count_sources(snapshot):
        checked.append(len(snapshot["frames"]))
        return original(snapshot)

    monkeypatch.setattr(scene_snapshot, "validate_scene_snapshot_bindings", count_sources)
    seen = []

    async def send(stage, payload, schema):
        seen.append((stage, deepcopy(payload), deepcopy(schema)))
        return answer(stage, payload)

    result = await write_relational_board(
        base.input.source,
        base,
        send=send,
        execution_policy=RelationalExecutionPolicy(minimum_interval_s=0),
    )
    assert result.receipt["version"] == "relational-diary-skeleton-v8"
    assert not result.receipt["execution"]["semantic_review_enabled"]
    assert [s for s, _, _ in seen].count("title") == 1 and seen[-1][0] == "title"
    assert "review" not in [s for s, _, _ in seen]
    assert checked == [len(result.prepared["snapshot"]["frames"])]
    tasks = {
        p[k]["id"]: p[k]
        for p in result.prepared["snapshot"]["plans"]
        for k in ("space_task", "action_task")
        if p[k]
    }
    for row in result.receipt["writing"]["results"]:
        brief = parse_brief(tasks[row["task_id"]]["payload"])
        assert row["request"] == brief_writer_view(brief)
        assert row["response_schema"] == brief_response_schema(brief)
        assert row["status"] == "returned" and row["semantic_status"] == "unverified"
    spaces = [p for s, p, _ in seen if s == "space"]
    relations = [r for p in spaces for slot in p.get("relation_slots", {}).values() for r in slot]
    assert relations and all("relationship" in r and "result" not in r for r in relations)
    assert result.receipt["writing"]["policy"] == "single-writing-brief-v5"
    assert len(spaces) >= 2 and spaces[1]["delivery_memory"]
    assert (
        spaces[1]["delivery_memory"][0]["selected_in_scene"]
        == spaces[0]["current"]["position"]["scene_id"]
    )
    actions = [p for s, p, _ in seen if s == "action"]
    assert actions and actions[0]["required_event"]["actor"]["entity_type"] == "dog"
    assert any(c["kind"] == "current_gait" for c in actions[0]["context_options"])
    assert not {"narration", "earlier", "delivery_memory", "companions"} & actions[0].keys()
    assert "registered_count" not in json.dumps(seen) and "changed_fields" not in json.dumps(seen)
    assert "source_bindings" not in json.dumps(seen) and "originals" not in json.dumps(seen)
    assert (
        result.receipt["title"]["request"]["scenes"][0]["space"]
        == result.receipt["cards"][0]["parts"]["space"]["text"]
    )
    assert not result.receipt["title"]["review_enabled"]
    assert writing_prompt("action", actions[0]).startswith("required_event")


@pytest.mark.parametrize("failure", ["rate_limit", "bad_id", "missing_event"])
async def test_failures_never_publish_candidates_or_retry(base, prepare, failure):
    calls = []

    async def send(stage, payload, schema):
        calls.append(stage)
        if failure == "rate_limit":
            raise ProviderFailure(429)
        value = json.loads(answer(stage, payload))
        if failure == "bad_id" and stage == "space":
            value["relation_ids"] = ["comparison:old-audit-id"]
        if failure == "missing_event" and stage == "action":
            value["evidence_ids"] = [payload["context_options"][0]["evidence"]["id"]]
        return json.dumps(value)

    result = await write_relational_board(
        base.input.source,
        base,
        prepare=prepare,
        send=send,
        execution_policy=RelationalExecutionPolicy(minimum_interval_s=0),
    )
    if failure == "rate_limit":
        assert calls == ["space"] and result.receipt["execution"]["stopped_on_rate_limit"]
    else:
        stage = "space" if failure == "bad_id" else "action"
        failed = [r for r in result.receipt["writing"]["results"] if r["stage"] == stage]
        assert failed and all(r["status"] == "failed" and r["raw_text"] for r in failed)
        assert all(c["parts"][stage]["text"] == "" for c in result.receipt["cards"])
    assert result.receipt["execution"]["automatic_retries"] == 0


@pytest.mark.parametrize("field", ["meaning", "actor", "phase", "plan"])
async def test_changed_brief_is_rejected_before_any_model_call(base, prepare, field):
    from daengs_backend.orchestration.relational_diary import generate_prepared_relational_diary

    value = await prepare(base)
    frames = value["snapshot"]["frames"]
    if field == "meaning":
        frames[0]["narrative_context"]["facts"][0]["time_meaning"] = "invented"
    elif field == "actor":
        frame = next(f for f in frames if f["action_brief"])
        frame["action_brief"]["required_event"]["actor"]["name"] = "unrecorded dog"
    elif field == "phase":
        value["snapshot"]["scene_positions"][frames[0]["scene_id"]]["timeline"]["ended_at"] = (
            "2099-01-01T00:00:00Z"
        )
    else:
        value["snapshot"]["plans"][0]["space_task"] = None
    value["revision"] = digest(value["snapshot"])
    send = AsyncMock()
    with pytest.raises(ValueError):
        await generate_prepared_relational_diary(value, send=send)
    send.assert_not_awaited()


async def test_configured_preparer_selects_brief_path(base, monkeypatch):
    from daengs_backend.services.walk_diary.collection import relational

    collect = AsyncMock(return_value={"prepared": True})
    monkeypatch.setattr(relational, "collect_and_prepare_relational", collect)
    await relational.configured_relational_preparation(base)
    assert collect.await_args.kwargs["writing_briefs"] is True


@pytest.mark.parametrize("first_fails", [False, True])
async def test_same_meaning_is_written_after_success_or_failure_in_real_sequence(
    base,
    public_collector,  # noqa: F811
    first_fails,
):
    async def collect(base, *, scene_ids=None):
        backgrounds = await public_collector(base.board)
        return prepare_relational_diary(
            replace(base, scene_backgrounds=backgrounds), scene_ids=scene_ids, writing_briefs=True
        )

    space_calls = []

    async def send(stage, payload, schema):
        if stage == "space":
            space_calls.append(payload)
            if first_fails and len(space_calls) == 1:
                raise ProviderFailure(503)
        return answer(stage, payload)

    result = await write_relational_board(
        base.input.source,
        base,
        prepare=collect,
        send=send,
        execution_policy=RelationalExecutionPolicy(minimum_interval_s=0),
    )
    assert len(space_calls) == len(result.receipt["cards"])
    if first_fails:
        assert "delivery_memory" not in space_calls[1]
        assert result.prepared["snapshot"]["plans"][1]["state_transition"] == "recover_introduction"
    else:
        assert space_calls[1]["delivery_memory"]
        assert result.prepared["snapshot"]["plans"][1]["state_transition"] == "maintain"
    assert all(c["parts"]["space"]["status"] == "returned" for c in result.receipt["cards"][1:])
