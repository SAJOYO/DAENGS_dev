"""Private APP comparison cannot substitute another source or move an existing scene."""

import copy

import pytest

from daengs_walk.diary_board_assembly import assemble_base_board
from daengs_walk.diary_board_output import publish_board
from daengs_walk.diary_board_selection import prepare_base_board
from daengs_walk.diary_input import digest
from daengs_walk.diary_slots import SlotPolicy, prepare_board_slots
from tests.walk.support.base_board import policy, saved_case
from tools.compare_diary_scenes import bind_snapshot, replay, slot_payload


def example():
    assembled, route, points = saved_case()
    source = assembled.source
    plan = prepare_base_board(source, policy(), route=route)
    board = assemble_base_board(source, plan, route=route)
    request = {
        "format": "diary-scene-comparison-input-v2",
        "owner_id": source.owner_id,
        "session_id": source.client_session_id,
        "scenes": [
            {
                "id": source.client_session_id + "/" + s.id,
                "at_millis": int(s.anchor.event_at.timestamp() * 1000),
                "point": [s.anchor.point.lat, s.anchor.point.lng] if s.anchor.point else None,
                "title": s.title,
                "body": s.body,
                "source_scene": s.model_dump(mode="json"),
            }
            for s in publish_board(board, plan).scenes
        ],
    }
    return source, route, points, plan, board, request


def test_visible_order_hidden_scenes_and_verbatim_edits_pass_through_shared_writer_context():
    source, route, _points, plan, original, request = example()
    request["scenes"] = list(reversed(request["scenes"][1:4]))
    request["scenes"][0]["body"] = "  직접 고친 원문\n줄바꿈 유지  "
    board = bind_snapshot(request, source, plan, original, digest(request))
    assert [s.id for s in board.scenes] == [s["source_scene"]["id"] for s in request["scenes"]]
    assert board.scenes[0].body == request["scenes"][0]["body"]
    for scene in board.scenes:
        saved = next(s for s in original.scenes if s.id == scene.id)
        assert (scene.anchor, scene.core_ref, scene.core) == (
            saved.anchor,
            saved.core_ref,
            saved.core,
        )
    slots = prepare_board_slots(source, board, SlotPolicy(), route=route)
    payload = slot_payload(board, slots)
    assert [s["scene_id"] for s in payload["scene_sequence"]] == [s.id for s in board.scenes]
    assert original.scenes[0].body != request["scenes"][0]["body"]


@pytest.mark.parametrize("changed", ["owner", "point", "time", "core", "duplicate", "empty_edit"])
def test_another_account_or_changed_source_never_enters_collection(changed):
    source, _route, _points, plan, board, original = example()
    request = copy.deepcopy(original)
    if changed == "owner":
        request["owner_id"] = "another-account"
    elif changed == "point":
        request["scenes"][1]["point"][0] += 0.001
    elif changed == "time":
        request["scenes"][1]["at_millis"] += 1000
    elif changed == "core":
        request["scenes"][1]["source_scene"]["core"]["version"] = "0" * 64
    elif changed == "duplicate":
        request["scenes"].append(request["scenes"][1])
    else:
        request["scenes"][1]["body"] = ""
    with pytest.raises(ValueError):
        bind_snapshot(request, source, plan, board, digest(request))


def test_changed_uploaded_route_is_rejected_before_replaying_motion():
    source, route, _points, _plan, _board, _request = example()
    points = list(route.evidence.accepted_points)
    points[0] = points[0].model_copy(update={"lat": points[0].lat + 0.001})
    with pytest.raises(ValueError, match="fingerprint"):
        replay(source, points, 5)
