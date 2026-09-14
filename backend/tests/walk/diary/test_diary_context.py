"""Context-only lookup must expose the same material as the actual card writer."""

import json
import subprocess
import sys
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from daengs_backend.services.walk_diary import runtime
from daengs_backend.services.walk_diary.writing import jobs
from daengs_backend.services.walk_diary.writing.context import (
    get_action_context,
    get_space_context,
)
from tests.walk.diary.test_diary_activity import prepared, provider
from tests.walk.support.paths import REPO


@pytest.mark.parametrize("pin,note", [(False, False), (True, False), (True, True)])
async def test_lookup_matches_sent_inputs_and_does_not_modify_prepared_slots(pin, note):
    base, _ = prepared(pin=pin, note=note)
    before = base.slots.model_dump(mode="json")
    expected = {}
    for scene in base.board.scenes:
        expected["space", scene.id] = get_space_context(base, scene.id).llm_input
        action = get_action_context(base, scene.id)
        if action is not None:
            expected["action", scene.id] = action.llm_input
    assert sum(stage == "action" for stage, _ in expected) == int(pin and not note)
    assert "물을 마시고 돌아가기로 했다." not in json.dumps(
        list(expected.values()), ensure_ascii=False
    )

    call = AsyncMock(side_effect=provider)
    result = await runtime.write_cards(base.input.source, base, generate=call)
    actual = {
        (j.stage, j.request["card_id"]): j.llm_request for j in result.jobs if j.stage != "title"
    }
    assert actual == expected
    assert all(j.failure_code is None for j in result.jobs)
    for item in expected.values():
        assert any(c.args[1] == item for c in call.call_args_list)
    assert base.slots.model_dump(mode="json") == before


@pytest.mark.parametrize("lookup", [get_action_context, get_space_context])
def test_lookup_rejects_foreign_cards_and_mixed_preparation(lookup):
    base, _ = prepared()
    with pytest.raises(ValueError, match="outside the prepared"):
        lookup(base, "card-from-another-walk")
    for field in ("input_revision", "plan_revision"):
        stale = replace(base, slots=base.slots.model_copy(update={field: "0" * 64}))
        with pytest.raises(ValueError, match="prepared source and slot version"):
            lookup(stale, base.board.scenes[0].id)
    stale_board = replace(base, board=base.board.model_copy(update={"plan_revision": "0" * 64}))
    with pytest.raises(ValueError, match="prepared source and slot version"):
        lookup(stale_board, base.board.scenes[0].id)


@pytest.mark.parametrize("make_job", [jobs.action_job, jobs.space_job])
def test_compatibility_entry_rejects_foreign_stamp_or_changed_scene(make_job):
    base, _ = prepared()
    scene = base.board.scenes[0]
    stamp = next(s for s in base.slots.stamps if s.scene_id == scene.id)
    foreign = next(s for s in base.slots.stamps if s.scene_id != scene.id)
    with pytest.raises(ValueError, match="selected scene and stamp"):
        make_job(base, scene, foreign)
    changed = scene.model_copy(update={"order": scene.order + 1})
    with pytest.raises(ValueError, match="selected scene and stamp"):
        make_job(base, changed, stamp)


def test_lookup_does_not_create_jobs_or_rerun_selection(monkeypatch):
    from daengs_walk.diary.route import movement
    from daengs_walk.diary.slots import service

    base, _ = prepared()

    def forbidden(*args, **kwargs):
        raise AssertionError("lookup tried to select or write")

    monkeypatch.setattr(jobs, "job", forbidden)
    monkeypatch.setattr(service, "prepare_board_slots", forbidden)
    monkeypatch.setattr(movement, "prepare_movement", forbidden)
    scene = next(s for s in base.board.scenes if s.core.kind == "user_record")
    context = get_action_context(base, scene.id)
    expected = context.llm_input
    context.llm_input["movement_context"]["meaning"] = "caller changed the preview"
    assert get_action_context(base, scene.id).llm_input == expected
    assert isinstance(get_space_context(base, scene.id).llm_input["materials"], list)


def test_cli_reads_saved_public_data_without_writer_or_network(tmp_path):
    script = """
import importlib.abc
import runpy
import socket
import sys

class NoWriter(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = (
            "daengs_backend.services.walk_diary.runtime",
            "daengs_backend.services.walk_diary.writing.jobs",
            "daengs_backend.services.walk_diary.writing.provider",
            "daengs_backend.orchestration.diary", "google.genai", "langgraph",
        )
        if any(fullname == p or fullname.startswith(p + ".") for p in forbidden):
            raise AssertionError("context-only lookup imported writer: " + fullname)

def no_network(*args, **kwargs):
    raise AssertionError("context-only lookup tried a network connection")

sys.meta_path.insert(0, NoWriter())
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
sys.path.insert(0, "tools")
sys.argv = ["tools/preview_diary_context.py", "--source",
            "evals/diary_route_scenario/public-02", "--output", sys.argv[1]]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
    output = tmp_path / "contexts"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script, str(output)],
        cwd=REPO / "backend",
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert {p.name for p in output.iterdir()} == {"contexts.json", "summary.json"}
    cards = json.loads((output / "contexts.json").read_text(encoding="utf-8"))
    assert len(cards) == 8
    assert sum(c["action"] is not None for c in cards) == 1
    saved = json.loads(
        (
            REPO / "backend/evals/diary_route_scenario/action-pin-boundary-01/llm-requests.json"
        ).read_text(encoding="utf-8")
    )
    expected = {(j["stage"], j["card_id"]): j["request"] for j in saved if j["stage"] != "title"}
    actual = {
        (stage, card["card_id"]): card[stage]
        for card in cards
        for stage in ("space", "action")
        if card[stage] is not None
    }
    for payload in actual.values():
        assert payload.pop("narration") == {
            "narrator": "이 산책을 기록한 보호자(나)",
            "companions": [{"name": "보리"}],
            "scope": "현재 장면",
        }
    # v6 fixtures stay unchanged; only shared narration is new, never slot facts.
    assert actual == expected
