"""A negotiated contract chooses the writer and completion before any result arrives."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from daengs_backend.schemas.walk_storyboard import (
    DiaryStoryboardResponse,
    StoryboardRequest,
    StoryboardResponse,
)
from daengs_backend.services import walk_storyboard_state as compatibility
from daengs_backend.services.walk_diary.api import legacy_slot_writer
from daengs_backend.services.walk_diary.contracts import CardWritingResult
from daengs_backend.services.walk_diary.legacy.slots import SlotWritingResult
from daengs_backend.services.walk_diary.lifecycle.strategy import select_strategy
from daengs_backend.services.walk_generation import state
from daengs_walk.diary.board.output import BOARD_FORMAT
from daengs_walk.diary.contracts.output import DiaryBundle
from daengs_walk.value_contracts import digest
from tests.walk.diary.test_diary_service_package import SOURCE, imports
from tests.walk.support.diary_generation import PATH, body


def test_shared_state_and_current_lifecycle_do_not_reenter_compatibility_modules():
    service_root = SOURCE / "daengs_backend/services"
    for path in service_root.rglob("*.py"):
        if path.name == "walk_storyboard_state.py":
            continue
        names = list(imports(path))
        assert not any(
            name.startswith("daengs_backend.services.walk_storyboard_state") for name in names
        ), path
    lifecycle = service_root / "walk_diary/lifecycle/generation.py"
    assert not any(".legacy." in name for name in imports(lifecycle))


def test_http_schemas_preserve_pre_extraction_hashes():
    golden = json.loads(
        (Path(__file__).parents[1] / "fixtures/generation-schema-v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert {
        model.__name__: digest(model.model_json_schema())
        for model in (StoryboardRequest, StoryboardResponse, DiaryStoryboardResponse)
    } == golden["schemas"]


def test_writer_override_never_changes_contract_and_legacy_opt_in_is_explicit():
    writer = AsyncMock()
    cards = select_strategy(BOARD_FORMAT, writer)
    slots = select_strategy(BOARD_FORMAT, legacy_slot_writer(writer))
    bundle = select_strategy("walk-diary-bundle-v1", legacy_slot_writer(writer))
    assert cards.write is slots.write is bundle.write is writer
    assert (cards.result_type, slots.result_type, bundle.result_type) == (
        CardWritingResult,
        SlotWritingResult,
        DiaryBundle,
    )
    for chosen, wrong in [
        (cards, SlotWritingResult),
        (slots, CardWritingResult),
        (bundle, CardWritingResult),
    ]:
        with pytest.raises(TypeError, match="selected generation contract"):
            chosen.validate(wrong.model_construct())
    writer.assert_not_called()


def test_wrong_slot_result_keeps_card_contract_and_publishes_existing_failure_base(api):
    from daengs_backend.routers.walk_storyboard import get_diary_writer

    client, current, _ = api
    # This writer returns real historical slot output. Without explicit opt-in it
    # must fail the card contract, not silently switch to historical completion.
    client.app.dependency_overrides[get_diary_writer] = lambda: current.writer
    response = client.post(PATH, json=body(current, bundle_format=BOARD_FORMAT))
    assert response.status_code == 200
    assert response.json()["bundle"]["failure_code"] == "provider_failed"
    current.writer.assert_awaited_once()


def test_state_compatibility_reexports_identical_transitions_and_exception_types():
    for name in ("reserve", "complete", "reusable", "StoryboardConflict", "StoryboardNotFound"):
        assert getattr(state, name) is getattr(compatibility, name)


def test_current_contract_selection_does_not_load_historical_runtime():
    script = """
import importlib.abc
import sys
class NoLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('daengs_backend.services.walk_legacy', 'daengs_backend.services.walk_diary.legacy', 'daengs_walk.storyboard')):
            raise AssertionError(fullname)
sys.meta_path.insert(0, NoLegacy())
from daengs_backend.services.walk_diary.lifecycle.strategy import select_strategy
from daengs_backend.schemas.walk_diary import DiaryStoryboardResponse
from daengs_backend.services.walk_generation import api
from daengs_backend.schemas.walk_generation import StoryboardRequest
selected = select_strategy('walk-diary-board-v1')
assert selected.result_type.__name__ == 'CardWritingResult'
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
