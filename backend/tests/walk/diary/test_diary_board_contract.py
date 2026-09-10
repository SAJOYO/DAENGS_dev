"""The fixture consumed by Android is produced by the actual saved-board pipeline."""

import json

import pytest

from daengs_backend.schemas.walk_storyboard import DiaryStoryboardResponse
from tests.walk.support.board_contract import board_contract
from tests.walk.support.paths import REPO


def test_android_fixture_stays_equal_to_public_board_projection():
    path = REPO / "backend/evals/walk-diary/board-v1.json"
    expected = json.loads(path.read_text(encoding="utf-8"))
    actual = board_contract()
    assert actual == expected
    assert DiaryStoryboardResponse.model_validate(actual).bundle.model_status == "accepted"
    assert {s["kind"] for s in actual["bundle"]["scenes"]} == {
        "session_boundary",
        "route_checkpoint",
        "user_record",
        "movement_observation",
    }


def test_response_cannot_mislabel_a_board_as_v1():
    value = board_contract()
    value["format"] = "walk-diary-response-v1"
    with pytest.raises(ValueError):
        DiaryStoryboardResponse.model_validate(value)
