"""Product rules: only pins open action prose, notes never feed generation."""

import gzip
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from daengs_backend.services.walk_diary import runtime
from daengs_backend.services.walk_diary.model_input import normalize
from daengs_backend.services.walk_diary.writing import assembly, jobs
from daengs_walk.diary.board.action_context import pin_movement
from daengs_walk.diary.board.output import publish_board
from daengs_walk.diary.contracts.input import digest
from tests.walk.diary.test_diary_activity import prepared, provider


@pytest.mark.parametrize("pin,note", [(False, False), (True, False), (True, True)])
async def test_pin_opens_exactly_one_job_regardless_of_abundant_movement(pin, note):
    base, _ = prepared(pin=pin, note=note)
    assert sum(bool(s.evidence) for s in base.slots.stamps) > 1
    call = AsyncMock(side_effect=provider)
    result = await runtime.write_cards(base.input.source, base, generate=call)
    count = int(pin and not note)
    assert sum(c.args[0] == "action" for c in call.call_args_list) == count
    assert sum(j.stage == "action" for j in result.jobs) == count
    assert sum(bool(c.writing.actions) for c in result.bundle.scenes) == count


def test_movement_only_request_is_rejected_by_construction_normalization_and_adoption():
    base, _ = prepared()
    scene = next(c for c in base.board.scenes if c.core.kind == "user_record")
    valid = jobs.action_job(base, scene)
    payload = {**valid.request, "action": None}
    with pytest.raises(ValueError, match="behavior pin required"):
        jobs.job("action", payload)
    with pytest.raises(ValueError, match="behavior pin required"):
        normalize("action", payload)
    forged = valid.model_copy(update={"request": payload})
    answer = {
        "card_id": scene.id,
        "request_revision": forged.request_revision,
        "text": "임의 이동",
        "action_id": None,
        "movement_ids": [],
    }
    assert jobs.validate_output(forged, answer).failure_code == "invalid_response"


async def test_movement_only_cache_is_ignored_and_foreign_action_cannot_be_attached():
    base, _ = prepared()
    old = await runtime.write_cards(base.input.source, base, generate=provider)
    action = next(j for j in old.jobs if j.stage == "action")
    raw = action.model_dump(mode="json")
    raw["request"]["action"] = None
    without_pin, _ = prepared(pin=False)
    cached = replace(without_pin, cached_jobs=(raw,))
    call = AsyncMock(side_effect=provider)
    result = await runtime.write_cards(cached.input.source, cached, generate=call)
    assert not any(c.args[0] == "action" for c in call.call_args_list)
    assert not any(c.writing.actions for c in result.bundle.scenes)
    public = publish_board(without_pin.board, without_pin.plan)
    with pytest.raises(ValueError, match="this behavior pin"):
        assembly.frozen_card(public.scenes[0], without_pin.slots.stamps[0], None, action)


def test_only_pin_phase_and_simultaneous_features_reach_the_writer():
    base, _ = prepared()
    scene = next(c for c in base.board.scenes if c.core.kind == "user_record")
    request = jobs.action_job(base, scene).request
    original = deepcopy(request)
    wire = normalize("action", request)
    assert set(wire.payload) == {"recorded_action", "movement_context", "narration"}
    assert wire.payload["recorded_action"]["id"] == "a1"
    assert wire.payload["movement_context"]["id"] == "m1"
    assert "느린" in wire.payload["movement_context"]["meaning"]
    assert not any(k in json.dumps(wire.payload) for k in ("from_s", "to_s", "phases", "extent"))
    for item in request["movement"]:
        facts = item["facts"]
        assert len(facts["phases"]) == 1
        phase = facts["phases"][0]
        assert phase["start_s"] <= facts["scene_at_s"] < phase["end_s"]
    assert request == original
    restored = wire.restore({"text": "냄새를 맡았다.", "evidence_ids": ["a1"]})
    assert not restored["movement_ids"]
    with pytest.raises(ValueError, match="omitted recorded action"):
        wire.restore({"text": "길을 걸었다.", "evidence_ids": ["m1"]})


def test_neighbor_turn_is_not_a_pin_event_and_no_nearest_phase_is_substituted():
    facts = {
        "scene_at_s": 10,
        "claims": [{"id": "straight"}, {"id": "later-turn", "event_s": 12}],
        "phases": [{"start_s": 0, "end_s": 20, "claims": ["straight", "later-turn"]}],
    }
    selected = pin_movement([{"id": "slot", "facts": facts}])
    assert selected[0]["facts"]["phases"][0]["claims"] == ["straight"]
    assert len(facts["claims"]) == 2
    assert not pin_movement([{"id": "slot", "facts": {**facts, "scene_at_s": 20}}])


@pytest.mark.parametrize("legacy", [False, True])
def test_park_is_a_background_with_internal_identity_preserved(legacy):
    facts = (
        {"name": "긴고유명공원", "reference": "registered_park_point", "distance_m": 42}
        if legacy
        else {
            "material": {"공원명": "긴고유명공원", "공원종류": "근린공원"},
            "relation": {"kind": "registered_park_point_distance", "distance_m": 42},
        }
    )
    request = {
        "materials": [
            {"id": "park-source", "role": "scene_registered_point_distance", "facts": facts}
        ]
    }
    original = deepcopy(request)
    model = normalize("space", request)
    text = json.dumps(model.payload, ensure_ascii=False)
    assert "공원" in text and "근처" in text
    assert all(v not in text for v in ("긴고유명", "근린", "등록 지점", "distance_m", "42"))
    assert request == original and model.references == {"m1": "park-source"}


def test_historical_movement_only_published_board_still_loads():
    from daengs_backend.services.walk_diary.storage.board import load_board

    path = (
        Path(__file__).resolve().parents[3]
        / "evals/diary_route_scenario/activity-offline-03/stored.json.gz"
    )
    raw = json.loads(gzip.decompress(path.read_bytes()))
    before = digest(raw)
    saved = load_board(raw)
    assert len(saved.bundle.scenes) == 8
    assert any(
        c.writing.actions and not c.writing.actions[0].action_id for c in saved.bundle.scenes
    )
    assert digest(raw) == before


async def test_lab_titles_use_generated_parts_and_archive_replay_keeps_original(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "tools"))
    from run_diary_final_titles import title_input

    base, _ = prepared(note=True)
    result = await runtime.write_cards(base.input.source, base, generate=provider)
    source = {"bundle": result.bundle.model_dump(mode="json")}
    notes = [c.writing.original_text for c in result.bundle.scenes if c.writing.original_text]
    assert notes
    current = title_input(source)
    historical = title_input(source, legacy=True)
    for note in notes:
        assert note not in json.dumps(current, ensure_ascii=False)
        assert note in json.dumps(historical, ensure_ascii=False)
