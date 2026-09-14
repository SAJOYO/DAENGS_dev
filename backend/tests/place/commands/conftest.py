import json
from pathlib import Path

import pytest

from daengs_evals.facility_tools.workspace import MemoryWorkspace
from daengs_evals.place_conversation.fixtures import FixtureSearcher, initial_filters
from daengs_place.place.commands.contract import FacilityState
from daengs_place.place.commands.executor import FacilityCommands


@pytest.fixture
async def workspace():
    data = Path(__file__).resolve().parents[3] / "evals/place_conversation"
    fixtures = json.loads((data / "fixtures.policy.v1.json").read_text(encoding="utf-8"))
    setup = json.loads((data / "cases.v1.jsonl").read_text(encoding="utf-8").splitlines()[0])[
        "setup"
    ]
    searcher = FixtureSearcher(setup, fixtures)
    port = MemoryWorkspace(
        FacilityState(filters=initial_filters(setup)), FacilityCommands(searcher)
    )
    await port.execute("initial", "search_places", {}, 0)
    return port
