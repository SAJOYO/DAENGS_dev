"""Stored readers and fixed pre-refactor wire contracts must survive writer extraction."""

import json
import subprocess
import sys

import pytest

from tests.walk.support.paths import REPO
from tests.walk.support.writing_boundary import fingerprints, fixed_cases


@pytest.fixture
async def cases(monkeypatch):
    return await fixed_cases(monkeypatch)


def test_fixed_public_storage_schema_and_hashes_match_before_refactor(cases):
    golden = json.loads(
        (REPO / "backend/evals/walk-diary/writing-boundary-v1.json").read_text(encoding="utf-8")
    )
    expected = {key: golden[key] for key in ("policy", "schemas", "cases")}
    assert fingerprints(cases) == expected


def test_stored_board_reader_is_independent_of_writer_runtime(cases):
    # A fresh interpreter cannot hide a dependency behind already imported test modules.
    script = """
import importlib.abc
import json
import sys
from types import SimpleNamespace

class NoWriterRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = (
            "daengs_backend.services.walk_diary_card_writing",
            "daengs_backend.services.walk_diary_card_provider",
            "daengs_backend.services.walk_diary_card_jobs",
            "daengs_backend.services.walk_diary_card_assembly",
            "daengs_backend.services.walk_diary_card_policy",
            "daengs_backend.services.walk_diary_board_provenance",
            "daengs_backend.services.walk_diary_board_slot_writing",
            "daengs_backend.services.walk_diary_slot_writing",
            "daengs_backend.orchestration", "daengs_backend.config",
            "google.genai", "openai", "langgraph",
        )
        if any(fullname == p or fullname.startswith(p + ".") for p in forbidden):
            raise AssertionError("stored reader imported writer runtime: " + fullname)

sys.meta_path.insert(0, NoWriterRuntime())
from daengs_backend.services.walk_diary_board_storage import load_board, read_board
from daengs_walk.diary_input import DiaryInput

data = json.load(sys.stdin)
prepared = SimpleNamespace(input=SimpleNamespace(source=DiaryInput.model_validate(data["source"])))
for case in data["cases"].values():
    raw = case["stored"]
    assert load_board(raw).model_dump(mode="json") == raw
    row = SimpleNamespace(bundle=raw, input_revision=raw["generation_revision"])
    assert read_board(prepared, row, "future-revision").model_dump(mode="json") == raw
    damaged = json.loads(json.dumps(raw))
    damaged["bundle"]["scenes"][0]["title"] = "tampered title"
    try:
        load_board(damaged)
    except ValueError:
        pass
    else:
        raise AssertionError("stored reader accepted changed content")
print("validated 6 stored variants without writer runtime")
"""
    child = subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps(cases),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert child.returncode == 0, child.stderr


def test_graph_does_not_import_writer_entry_or_provider():
    script = """
import importlib.abc
import sys

class NoEntry(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {
            "daengs_backend.services.walk_diary_card_writing",
            "daengs_backend.services.walk_diary_card_provider",
            "google.genai",
        }:
            raise AssertionError("graph imported entry/provider: " + fullname)

sys.meta_path.insert(0, NoEntry())
from daengs_backend.orchestration.diary import DiaryOrchestrationService
assert callable(DiaryOrchestrationService.run)
"""
    child = subprocess.run(
        [sys.executable, "-c", script], text=True, capture_output=True, timeout=30, check=False
    )
    assert child.returncode == 0, child.stderr


def test_compatibility_imports_are_the_same_contract_objects():
    from daengs_backend.services import walk_diary_card_contracts as contracts
    from daengs_backend.services import walk_diary_card_writing as writing

    for name in (
        "SpaceProse",
        "ActionProse",
        "CardTitle",
        "CardTitles",
        "WritingJob",
        "CardWritingResult",
    ):
        assert getattr(writing, name) is getattr(contracts, name)
