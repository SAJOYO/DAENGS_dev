"""Current diary calculations must not load legacy selection or inherit its policy."""

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from daengs_walk.diary.route.movement import pace_claims
from daengs_walk.diary.route.movement_policy import MovementPolicy
from daengs_walk.diary.route.observations import OBSERVATION_PACE
from daengs_walk.route.pace import movement_candidates, session_speed_baseline
from tests.walk.diary.test_diary_service_package import SOURCE, imports
from tests.walk.support.route_contracts import results


def test_pre_extraction_shared_geometry_observation_and_board_hashes():
    golden = json.loads(
        (Path(__file__).parents[1] / "fixtures/route-boundary-v1.json").read_text(encoding="utf-8")
    )

    # #520 deliberately replaces diary movement shapes and their versioned proof.
    # test_diary_movement_materials checks those semantics; shared calculations,
    # observation selection, board selection and legacy output must remain byte-identical.
    def shared(cases):
        return {
            name: {k: v for k, v in case.items() if k != "movement"} for name, case in cases.items()
        }

    assert shared(results()) == shared(golden["cases"])


def test_shared_calculations_and_diary_do_not_import_legacy_policy():
    for folder in ("route", "diary"):
        for path in (SOURCE / "daengs_walk" / folder).rglob("*.py"):
            names = list(imports(path))
            assert not any(name.startswith("daengs_walk.storyboard") for name in names), path
            if folder == "route":
                assert not any(
                    name.startswith(("daengs_walk.diary", "daengs_backend")) for name in names
                ), path


def test_diary_route_and_slot_imports_with_legacy_blocked():
    script = """
import importlib.abc
import sys
class NoLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('daengs_walk.storyboard'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, NoLegacy())
from daengs_walk.diary.route import binding, observations, movement
from daengs_walk.diary.selection import board
from daengs_walk.diary.slots import sources
assert observations.build_observation_pool
assert movement.prepare_movement
"""
    run = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert run.returncode == 0, run.stderr


def nodes(speed, seconds=20, block=0):
    return [
        {"block": block, "start_s": 0, "elapsed_s": seconds, "duration_s": seconds, "speed": speed}
    ]


@pytest.mark.parametrize(
    "speed,seconds,count",
    [(0.5, 20, 0), (1.75, 20, 0), (0.49, 20, 1), (1.76, 20, 1), (1.76, 19, 0)],
)
def test_canonical_observation_thresholds_stay_strict(speed, seconds, count):
    assert (
        len(movement_candidates(nodes(speed, seconds), 1, "session_speed", policy=OBSERVATION_PACE))
        == count
    )


def test_consumer_policy_changes_do_not_leak_to_other_consumers(monkeypatch):
    from daengs_walk import storyboard_selection as legacy

    sample = nodes(1.6, 10)
    assert not movement_candidates(sample, 1, "session_speed", policy=OBSERVATION_PACE)
    assert pace_claims(sample, 1, MovementPolicy(), "source")
    monkeypatch.setattr(
        legacy,
        "STORYBOARD_PACE",
        replace(legacy.STORYBOARD_PACE, fast_ratio=1.1, minimum_seconds=1),
    )
    assert legacy.movement_candidates(sample, 1, "session_speed")
    assert not movement_candidates(sample, 1, "session_speed", policy=OBSERVATION_PACE)


def test_baseline_filter_sample_count_and_block_separation():
    assert session_speed_baseline(nodes(1) * 4, minimum_speed=0.5, minimum_samples=5) is None
    assert (
        session_speed_baseline(nodes(0.49) + nodes(0.5) * 5, minimum_speed=0.5, minimum_samples=5)
        == 0.5
    )
    split = nodes(2, 10) + nodes(2, 10, block=1)
    assert not movement_candidates(split, 1, "session_speed", policy=OBSERVATION_PACE)
