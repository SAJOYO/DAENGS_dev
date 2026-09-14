"""Geometry contrasts and pin-time consumption, without model or public API calls."""

import gzip
import json
import math
import uuid
from copy import deepcopy

import pytest

from daengs_backend.services.walk_diary.model_input import normalize
from daengs_walk.diary.board.activity import activity_projection, movement_uses
from daengs_walk.diary.route.geometry import RoutePatternPolicy
from daengs_walk.diary.route.movement import phases_for, prepare_movement
from daengs_walk.diary.route.movement_geometry import movement_shapes
from daengs_walk.diary.route.movement_policy import MovementPolicy
from daengs_walk.evidence import analyze_walk
from tests.walk.diary.test_diary_activity import prepared
from tests.walk.support.route_patterns import route


def arc(*, reverse=False, mirror=False, rotate=False, stretch=1, clipped=False):
    points = [(0, 0), (60, 0)] if not clipped else []
    points += [
        (60 + 80 * math.sin(math.radians(t)), 80 * (1 - math.cos(math.radians(t))))
        for t in range(0, 91, 5)
    ]
    if not clipped:
        points.append((140, 140))
    # Remove duplicated entry coordinate.
    points = [p for i, p in enumerate(points) if not i or p != points[i - 1]]
    if reverse:
        points += points[-2::-1]
    if mirror:
        points = [(x, -y) for x, y in points]
    if rotate:
        points = [(-y, x) for x, y in points]
    return [(i * 30 * stretch, x, y) for i, (x, y) in enumerate(points)]


def shapes(waypoints, **kwargs):
    sample = route("shape-contrast", waypoints, **kwargs)
    evidence = analyze_walk(uuid.UUID(int=1), sample.started_at, sample.ended_at, sample.points)
    return movement_shapes(evidence, RoutePatternPolicy())[0]


def kinds(values):
    return [v["meaning"] for v in values]


def test_arc_and_single_corner_have_distinct_shape_and_event():
    curved = shapes(arc())
    corner = shapes([(0, 0, 0), (120, 120, 0), (240, 120, 120)])
    assert "curve_left" in kinds(curved)
    assert not any(v["is_event"] for v in curved)
    assert kinds(corner).count("turn_left") == 1
    assert "curve_left" not in kinds(corner)


@pytest.mark.parametrize(
    "rotate,mirror,stretch", [(True, False, 1), (False, True, 1), (False, False, 2)]
)
def test_shape_is_relative_to_progress_and_independent_of_time(rotate, mirror, stretch):
    found = kinds(shapes(arc(rotate=rotate, mirror=mirror, stretch=stretch)))
    assert ("curve_right" if mirror else "curve_left") in found


def test_clipped_curve_keeps_observed_shape_without_inventing_complete_turn():
    found = shapes(arc(clipped=True))
    assert "curve_left" in kinds(found)
    assert not any(x["is_event"] for x in found)


def test_s_bend_and_two_corners_do_not_collapse_into_straight_or_one_curve():
    corner = shapes([(0, 0, 0), (90, 90, 0), (180, 90, 90), (270, 0, 90)])
    assert kinds(corner).count("turn_left") == 2
    # Two broad curves with opposite signs, ending in the original heading.
    pts = arc(clipped=True)
    end_t, end_x, end_y = pts[-1]
    pts += [
        (
            end_t + i * 30,
            end_x + 80 * (1 - math.cos(math.radians(t))),
            end_y + 80 * math.sin(math.radians(t)),
        )
        for i, t in enumerate(range(5, 91, 5), 1)
    ]
    found = kinds(shapes(pts))
    assert "curve_left" in found and "curve_right" in found


def test_retrace_preserves_current_direction_curve_and_rejects_parallel_path():
    found = kinds(shapes(arc(reverse=True)))
    assert "curve_left" in found and "curve_right" in found and "retrace" in found
    parallel = shapes([(0, 0, 0), (120, 120, 0), (160, 120, 40), (280, 0, 40)])
    assert "retrace" not in kinds(parallel)
    nearby_parallel = shapes([(0, 0, 0), (120, 120, 0), (130, 120, 5), (250, 0, 5)])
    assert "retrace" not in kinds(nearby_parallel)


def test_sparse_turn_does_not_claim_a_corner_or_curve():
    found = kinds(shapes([(0, 0, 0), (60, 60, 0), (120, 60, 60)], step_s=60))
    assert "direction_left" in found
    assert "curve_left" not in found and "turn_left" not in found


def request(start=0, end=100, slow_start=80, slow_end=100, *, action=True):
    claims = [
        {"id": "curve", "kind": "path", "meaning": "curve_right", "start_s": 0, "end_s": 100},
        {"id": "return", "kind": "path", "meaning": "retrace", "start_s": 0, "end_s": 100},
        {
            "id": "slow",
            "kind": "pace",
            "meaning": "relative_slow",
            "start_s": slow_start,
            "end_s": slow_end,
        },
    ]
    return {
        "card_id": "card",
        "request_revision": "revision",
        "movement": [
            {
                "id": "slot",
                "facts": {
                    "format": "diary-movement-material-v1",
                    "scene_at_s": 90,
                    "claims": claims,
                    "phases": phases_for(claims, start, end),
                },
            }
        ],
        "action": {
            "id": "pin",
            "kind": "sniffing",
            "actor": {"name": "보리"},
            "material": {"무엇을": "냄새 맡기"},
        }
        if action
        else None,
    }


def test_projection_uses_pin_time_not_card_extent_or_past_pace():
    full, _ = activity_projection(request())
    part, _ = activity_projection(request(80, 100))
    before, _ = activity_projection(request(0, 70))
    assert full == part
    f = full["movement_context"]
    assert "오른쪽" in f["meaning"] and "되짚" in f["meaning"] and "느린" in f["meaning"]
    assert "movement_context" not in before
    middle, _ = activity_projection(request(slow_start=80, slow_end=90))
    assert "느린" not in middle["movement_context"]["meaning"]


def test_earlier_pace_change_does_not_enter_pin_context():
    raw = request()
    original, _ = activity_projection(raw)
    facts = raw["movement"][0]["facts"]
    facts["claims"].append(
        {
            "id": "earlier-slow",
            "kind": "pace",
            "meaning": "relative_slow",
            "start_s": 10,
            "end_s": 25,
        }
    )
    facts["phases"] = phases_for(facts["claims"], 0, 100)
    wire, _ = activity_projection(raw)
    assert wire == original


def test_actual_wire_has_no_numeric_analysis_and_restores_composed_citations():
    raw = request(action=True)
    model = normalize("action", raw)
    text = json.dumps(model.payload, ensure_ascii=False)
    for forbidden in (
        "from_s",
        "to_s",
        "at_s",
        "support",
        "start_s",
        "end_s",
        "phases",
        "0.5",
        "10초",
    ):
        assert forbidden not in text
    material = model.payload["movement_context"]
    flow = model.restore(
        {
            "text": "보리의 냄새 맡기를 기록했다.",
            "evidence_ids": ["a1"],
        }
    )
    pace_ids = {u["id"] for u in movement_uses(raw) if u["kind"] == "pace"}
    assert not set(flow["movement_ids"]) & pace_ids
    combined = model.restore(
        {
            "text": "굽은 구간을 천천히 되짚던 무렵 보리의 냄새 맡기를 기록했다.",
            "evidence_ids": [material["id"], "a1"],
        }
    )
    assert pace_ids <= set(combined["movement_ids"])
    assert len(combined["movement_ids"]) == len(set(combined["movement_ids"]))
    assert set(model.payload) == {"recorded_action", "movement_context"}


def test_behavior_content_does_not_change_gps_analysis_or_movement_identity():
    base, verified = prepared()
    before = prepare_movement(base.input.source, verified, MovementPolicy())
    changed = base.input.source.model_copy(update={"records": ()})
    after = prepare_movement(changed, verified, MovementPolicy())
    assert before == after
    without = request(action=False)
    with_pin = deepcopy(without)
    with_pin["action"] = request(action=True)["action"]
    assert movement_uses(without) == movement_uses(with_pin)
    with pytest.raises(ValueError, match="behavior pin required"):
        activity_projection(without)
    assert activity_projection(with_pin)[0]["movement_context"]


def test_turn_only_enters_context_at_its_event_time():
    raw = request(slow_start=80)
    facts = raw["movement"][0]["facts"]
    facts["claims"] = [
        {"id": "path", "kind": "path", "meaning": "straight_run", "start_s": 0, "end_s": 100},
        {
            "id": "turn",
            "kind": "path",
            "meaning": "turn_right",
            "start_s": 20,
            "end_s": 70,
            "event_s": 50,
        },
    ]
    facts["phases"] = phases_for(facts["claims"], 0, 100)
    wire, _ = activity_projection(raw)
    assert wire["movement_context"]["meaning"] == "대체로 곧게 이동"
    facts["scene_at_s"] = 50
    at_turn, _ = activity_projection(raw)
    assert "오른쪽" in at_turn["movement_context"]["meaning"]
    assert "곧게" not in at_turn["movement_context"]["meaning"]


@pytest.mark.parametrize(
    "sample", ["activity-offline-03", "movement-materials-03", "movement-gemini-01"]
)
def test_old_activity_receipts_remain_readable_with_original_input(sample):
    from daengs_backend.services.walk_diary.storage.board import load_board
    from tests.walk.support.paths import REPO

    path = REPO / "backend/evals/diary_route_scenario" / sample / "stored.json.gz"
    raw = json.loads(gzip.decompress(path.read_bytes()))
    loaded = load_board(raw)
    assert len(loaded.bundle.scenes) == 8
