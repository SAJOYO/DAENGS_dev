"""Keep diary planning usable without backend execution or unrelated domain stages."""

import subprocess
import sys

import pytest

from tests.walk.diary.test_diary_service_package import SOURCE, imports

PREFIX = "daengs_walk.diary"
PACKAGE = SOURCE / "daengs_walk/diary"


def test_domain_does_not_call_product_services_or_external_providers():
    forbidden = (
        "daengs_backend",
        "daengs_life",
        "daengs_place",
        "daengs_journey",
        "fastapi",
        "sqlalchemy",
        "httpx",
        "requests",
        "aiohttp",
        "redis",
        "google",
        "openai",
        "langgraph",
        "langchain",
    )
    violations = {
        str(path.relative_to(PACKAGE)): [
            name for name in imports(path) if name.split(".", 1)[0] in forbidden
        ]
        for path in PACKAGE.rglob("*.py")
    }
    assert not {path: names for path, names in violations.items() if names}


@pytest.mark.parametrize(
    ("area", "forbidden"),
    [
        ("contracts", ("board", "selection", "space", "slots", "legacy", "cli")),
        ("route", ("board", "selection", "space", "slots", "legacy", "cli")),
        ("space", ("board", "selection", "route", "slots", "legacy", "cli")),
        ("selection", ("slots", "legacy", "cli", "board.preview", "board.assembly")),
        ("slots", ("selection", "legacy", "cli", "board.preview", "board.assembly")),
        ("board", ("legacy", "cli")),
    ],
)
def test_planning_dependencies_follow_ownership(area, forbidden):
    denied = tuple(PREFIX + "." + name for name in forbidden)
    violations = {
        str(path.relative_to(PACKAGE)): [
            name
            for name in imports(path)
            if any(name == p or name.startswith(p + ".") for p in denied)
        ]
        for path in (PACKAGE / area).rglob("*.py")
    }
    assert not {path: names for path, names in violations.items() if names}


def test_receipt_contracts_validate_without_board_selection_or_slot_execution():
    # Import blocking in a fresh interpreter also catches transitive dependencies.
    script = """
import importlib.abc
import sys

class NoPlanningRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = (
            'daengs_backend', 'daengs_walk.diary.board', 'daengs_walk.diary.selection',
            'daengs_walk.diary.space', 'daengs_walk.diary.slots',
            'daengs_walk.diary.legacy', 'daengs_walk.diary.cli',
        )
        if any(fullname == p or fullname.startswith(p + '.') for p in forbidden):
            raise AssertionError('receipt imported planning runtime: ' + fullname)

sys.meta_path.insert(0, NoPlanningRuntime())
from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.contracts.slots import BoardSlotSnapshot, SlotPolicy
from daengs_walk.diary.contracts.slot_receipt import StoredSceneWriting

snapshot = BoardSlotSnapshot(
    client_session_id='session', input_revision=digest('input'),
    plan_revision=digest('plan'), policy=SlotPolicy(), stamps=(),
)
assert BoardSlotSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
assert snapshot.revision() == digest({
    'board': snapshot.plan_revision,
    'policy': snapshot.policy.model_dump(mode='json'), 'stamps': [],
})
assert StoredSceneWriting.model_json_schema()['type'] == 'object'
"""
    result = subprocess.run(
        [sys.executable, "-c", script], text=True, capture_output=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
