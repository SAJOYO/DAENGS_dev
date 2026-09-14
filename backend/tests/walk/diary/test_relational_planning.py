import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from daengs_walk.diary.relational.contracts import ActionInput, SpaceInput
from daengs_walk.diary.relational.planning import make_plan


def frame(at, cover="길", block=0, action=False):
    return {
        "at_s": at,
        "block": block,
        "scene_id": str(at),
        "anchor": {"event_at": f"2026-09-14T10:{at:02d}:00+09:00"},
        "space": {
            "materials": [
                {
                    "id": "m1",
                    "role": "point_land_cover",
                    "material": {"피복": cover},
                    "relation": "현재 점",
                }
            ]
        },
        "action": {
            "recorded_action": {"id": "a1", "actor": "보리", "action": "냄새 맡기"},
            "movement_context": {
                "id": "m1",
                "meaning": "상대적으로 느림",
                "relation": "핀 시각의 기기 이동",
            },
        }
        if action
        else None,
    }


def test_spatial_history_and_current_action_are_separate():
    p = make_plan(frame(20, "숲", action=True), frame(0, "길"), None)
    space, action = p["space_task"]["payload"], p["action_task"]["payload"]
    assert space["relations"][0]["before"] == {"피복": "길"}
    assert action["current_space"][0]["material"] == {"피복": "숲"}
    assert "action" not in space and "movement_context" not in space
    assert "relations" not in action and "before" not in action
    assert action["current_space"][0]["id"] != action["movement_context"]["id"]
    with pytest.raises(ValidationError):
        ActionInput.model_validate({**action, "relations": space["relations"]})
    with pytest.raises(ValidationError):
        SpaceInput.model_validate({**space, "recorded_action": action["recorded_action"]})


def test_same_space_suppresses_space_job_but_repeated_pin_survives():
    first = make_plan(frame(0, action=True), None, None)
    second = make_plan(frame(20, action=True), frame(0, action=True), None, first["state_after"])
    assert second["state_transition"] == "maintain"
    assert second["space_task"] is None
    assert second["action_task"]["id"] != first["action_task"]["id"]
    assert "action" not in second["state_after"]


def test_missing_space_suspends_without_claiming_departure():
    current = frame(20)
    current["space"]["materials"] = []
    plan = make_plan(current, frame(0), None)
    assert plan["space_task"] is None
    assert plan["state_transition"] == "suspend"
    assert plan["space_relations"][0]["kind"] == "unconfirmed"
    assert plan["state_after"]["active_context"] == {}


def test_future_events_excluded_and_observations_do_not_open_action():
    catalog = SimpleNamespace(
        claims=[
            {
                "id": "before",
                "block": 0,
                "start_s": 0,
                "end_s": 30,
                "event_s": 10,
                "meaning": "turn_left",
            },
            {
                "id": "future",
                "block": 0,
                "start_s": 15,
                "end_s": 40,
                "event_s": 30,
                "meaning": "turn_right",
            },
        ]
    )
    p = make_plan(frame(20), frame(0), catalog)
    assert len(p["movement_observations"]) == 1
    assert p["movement_observations"][0]["at_pin_s"] == -10
    assert p["action_task"] is None
    assert p["space_task"] is None
    assert make_plan(frame(20, block=1), frame(0), catalog)["movement_observations"] == []


def test_zero_writing_budget_preserves_eligible_facts():
    from daengs_walk.diary.contracts.slots import SlotPolicy
    from daengs_walk.diary.slots.admission import admit
    from tests.walk.diary.test_diary_slots import prepare

    candidates = list(prepare().stamps[1].evidence)
    eligible = []
    assert not admit(
        "scene", candidates, [], SlotPolicy(total_slots=0), eligible_out=eligible
    ).evidence
    assert {e.id for e in eligible} == {e.id for e in candidates}


async def test_working_skeleton_failure_originals_and_saved_read(tmp_path):
    from daengs_backend.orchestration.relational_diary import generate_relational_skeleton
    from daengs_backend.services.walk_diary.preparation.board import assemble_saved_base_board
    from daengs_backend.services.walk_diary.preparation.input import InputAssembly
    from daengs_backend.services.walk_diary.preparation.observations import ObservationSource
    from daengs_backend.services.walk_diary.storage.relational import read_skeleton, save_skeleton
    from daengs_backend.services.walk_diary.writing.relational import validate_prepared
    from daengs_evals.diary_slots_demo import demo_input
    from tests.walk.support.base_board import policy

    source, route, _ = demo_input()
    base = assemble_saved_base_board(
        InputAssembly(source, (), ObservationSource(route.version, evidence=route.evidence)),
        policy(3),
    )
    seen = []

    async def send(stage, payload, schema):
        seen.append((stage, deepcopy(payload)))
        if stage == "title":
            return json.dumps({"title": "산책 기록"})
        if stage == "space":
            raise TimeoutError("simulated failure")
        return json.dumps(
            {"text": "행동 기록.", "evidence_ids": [payload["recorded_action"]["id"]]}
        )

    roads = [{"point": s.anchor.point.model_dump(mode="json"), "addr_type": 10,
              "response": {"errCd": 0, "result": [{"road_nm": f"시험로{i}길"}]}}
             for i, s in enumerate(base.board.scenes) if s.anchor.point is not None]
    result = await generate_relational_skeleton(base, send=send, road_snapshots=roads)
    assert seen[-1][0] == "title"
    originals = result["prepared"]["snapshot"]["originals"]
    assert originals
    for original in originals:
        content = original["record"]["content"]
        if content["kind"] == "note":
            assert content["text"] not in json.dumps(seen, ensure_ascii=False)
    assert any(c["parts"]["space"]["status"] == "failed" for c in result["receipt"]["cards"])
    path = tmp_path / "receipt.json"
    save_skeleton(path, result)
    assert read_skeleton(path) == result["receipt"]
    changed = deepcopy(result["prepared"])
    changed["snapshot"]["input_revision"] = "changed"
    with pytest.raises(ValueError):
        validate_prepared(changed)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["payload"]["receipt"]["title"]["text"] = "changed"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        read_skeleton(path)
