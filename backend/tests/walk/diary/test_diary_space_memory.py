"""Spatial memory changes, distinct source scopes and capacity-only comparison."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from daengs_walk.diary.cli.space_replay import load_replay, main
from daengs_walk.diary.contracts.input import Point, digest
from daengs_walk.diary.slots.memory import replay_spaces
from daengs_walk.diary.space.coverage import PreparedFeature
from daengs_walk.diary.space.geometry import distance, feature_contains
from daengs_walk.diary.space.materials import normalize_spaces
from daengs_walk.diary.space.policy import SpacePolicy, apply_space
from tests.walk.diary.test_diary_space_materials import (
    POINT,
    area,
    land,
    moved,
    park,
    ring,
    shop,
)

START = datetime(2026, 9, 1, tzinfo=UTC)


def materials(*, parks=None, cover=None):
    return normalize_spaces(
        {
            "point": POINT,
            "commerce": area([shop(i, east=350 + i) for i in range(12)]),
            "park": area([park(1)] if parks is None else parks, "park"),
            "land_cover": land(rings=[ring(-400, -400, 400, 400)]) if cover is None else cover,
        }
    )


def replay(points, *, value=None, batches=None, radius=100, comparison=None):
    data = {
        "policy": {"park_radius_m": radius},
        "points": [
            {"seq": i, "at": START + timedelta(seconds=i), "point": p} for i, p in enumerate(points)
        ],
        "batches": batches
        if batches is not None
        else [
            {
                "id": "initial",
                "available_seq": 0,
                "materials": materials() if value is None else value,
            }
        ],
    }
    if comparison is not None:
        data["comparison"] = comparison
    return replay_spaces(data)


def loaded(frame):
    return {r.source for r in frame.loaded}


def test_partial_exit_and_reentry_preserve_other_background_and_frozen_frames():
    value = materials()
    before = value.model_dump_json()
    result = replay([POINT, moved(150), moved(450), moved(550), POINT], value=value)
    assert [loaded(f) for f in result.frames] == [
        {"park", "commerce", "land_cover"},
        {"commerce", "land_cover"},
        {"commerce"},
        set(),
        {"park", "commerce", "land_cover"},
    ]
    assert [(c.source, c.reason) for c in result.frames[1].changes if c.change == "evicted"] == [
        ("park", "outside_park_distance")
    ]
    assert all(c.change == "loaded" for c in result.frames[-1].changes)
    assert value.model_dump_json() == before
    assert result.frames[0].loaded == result.frames[-1].loaded


def test_commerce_keeps_original_sample_not_false_current_nearest_or_cluster_membership():
    result = replay([POINT, moved(200)])
    a, b = [next(r for r in f.loaded if r.source == "commerce") for f in result.frames]
    assert a.material == b.material
    assert b.relation["sample_center"] == POINT
    assert b.relation["sample_center_to_nearest_registration_m"] == pytest.approx(350, abs=0.01)
    assert b.relation["current_offset_from_sample_center_m"] == pytest.approx(200, abs=0.01)
    assert "nearest_registered_point_m" not in b.relation
    decision = next(d for d in result.frames[1].decisions if d.key == b.key)
    assert decision.application.details["local_cluster_membership"] == "not_determined"


def test_exact_park_cutoff_and_updated_distance_do_not_infer_park_boundary():
    item = next(m for m in materials().materials if m.source == "park")
    p = moved(100.0004)
    metres = distance(POINT, p)
    policy = SpacePolicy(park_radius_m=metres)
    assert apply_space(item, Point(**p), policy).eligibility == "pass"
    assert apply_space(item, Point(**moved(101)), policy).reason == "outside_park_distance"
    result = replay([POINT, p], radius=metres)
    assert "park" in loaded(result.frames[1])
    assert any(e.facts["source"] == "park" for e in result.frames[1].capacity_stamp.evidence)
    park_relation = next(r.relation for r in result.frames[1].loaded if r.source == "park")
    assert park_relation == {"kind": "registered_park_point_distance", "distance_m": metres}


def test_feature_hole_exit_boundary_and_reentry_are_independent_of_other_sources():
    cover = land(rings=[ring(-400, -400, 400, 400), ring(50, -20, 100, 20)])
    result = replay([POINT, moved(75), moved(50), moved(101)], value=materials(cover=cover))
    assert ["land_cover" in loaded(f) for f in result.frames] == [True, False, True, True]
    assert all("commerce" in loaded(f) for f in result.frames)


def test_source_failure_does_not_clear_valid_archive_and_future_batches_do_not_leak():
    initial = materials()
    failed = normalize_spaces({"point": POINT, "land_cover": {**land(), "response": {}}})
    result = replay(
        [POINT, POINT, POINT],
        batches=[
            {"id": "initial", "available_seq": 1, "materials": initial},
            {"id": "failed", "available_seq": 2, "materials": failed},
        ],
    )
    assert not result.frames[0].loaded
    assert result.frames[1].loaded == result.frames[2].loaded
    assert any(a["reason"] == "invalid_source_response" for a in result.frames[2].input_audit)


def test_unlocated_and_expiry_drop_scene_relations_without_borrowing_last_point():
    value = materials()
    result = replay(
        [POINT, None, POINT, POINT],
        batches=[
            {
                "id": "initial",
                "available_seq": 0,
                "materials": value,
                "expires_at": START + timedelta(seconds=3),
            }
        ],
    )
    assert not result.frames[1].loaded
    assert {c.reason for c in result.frames[1].changes} == {"scene_unlocated"}
    assert result.frames[2].loaded == result.frames[0].loaded
    assert not result.frames[3].loaded
    assert {c.reason for c in result.frames[3].changes} == {"batch_expired"}


def test_capacity_loss_does_not_remove_memory_or_force_single_background():
    result = replay([POINT, POINT], value=materials(parks=[park(i) for i in range(5)]))
    for frame in result.frames:
        assert len(frame.loaded) == 7
        assert len(frame.capacity_stamp.evidence) == 3
        assert sum(d.admission == "part_capacity" for d in frame.capacity_stamp.decisions) == 4
        assert {e.facts["source"] for e in frame.capacity_stamp.evidence} == loaded(frame)
    assert all(c.change == "retained" for c in result.frames[1].changes)


def test_conflicting_location_does_not_win_by_nearness_or_arrival_order():
    original, changed = materials(), materials(parks=[park(1, east=350)])
    batches = [
        {"id": "original", "available_seq": 0, "materials": original},
        {"id": "changed", "available_seq": 1, "materials": changed},
    ]
    result = replay([POINT, POINT], batches=batches)
    assert loaded(result.frames[1]) == {"commerce", "land_cover"}
    assert any(c.reason == "conflicting_materials" for c in result.frames[1].changes)
    assert result.frames == replay([POINT, POINT], batches=list(reversed(batches))).frames


def test_duplicate_batch_keeps_one_assertion_and_all_references():
    value = materials()
    result = replay(
        [POINT],
        batches=[
            {"id": "b", "available_seq": 0, "materials": value},
            {"id": "a", "available_seq": 0, "materials": value},
        ],
    )
    assert len(result.frames[0].loaded) == 3
    assert all(len(r.references) == 2 for r in result.frames[0].loaded)


@pytest.mark.parametrize("field", ["dictionary", "hash", "scope", "kind"])
def test_changed_normalized_input_fails_before_any_stamp(field):
    value = materials().model_dump(mode="json")
    if field == "dictionary":
        value["dictionary_version"] = "0" * 64
    elif field == "hash":
        value["materials"][0]["support"]["nearest_registered_point_m"] = 0
    else:
        item = value["materials"][0]
        if field == "kind":
            item["scope"]["kind"] = "current_cluster_membership"
        else:
            item["scope"]["point"] = moved(10)
        item["id"] = "space:" + digest({k: v for k, v in item.items() if k != "id"})
    with pytest.raises(ValueError):
        replay([POINT], value=value)


def test_cli_relative_files_replay_and_refuse_overwrite(tmp_path, monkeypatch, capsys):
    import json

    root = tmp_path / "inputs"
    root.mkdir()
    value = materials().model_dump(mode="json")
    (root / "materials.json").write_text(json.dumps(value), encoding="utf-8")
    raw = {
        "policy": {"park_radius_m": 100},
        "points": [{"seq": 0, "at": START.isoformat(), "point": POINT}],
        "batches": [{"id": "saved", "available_seq": 0, "materials": "materials.json"}],
    }
    path, output = root / "input.json", tmp_path / "result.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert load_replay(path)["batches"][0]["materials"] == value
    monkeypatch.setattr("sys.argv", ["replay", "--input", str(path), "--output", str(output)])
    main()
    assert json.loads(output.read_text(encoding="utf-8"))["frames"][0]["loaded"]
    assert '"frames": 1' in capsys.readouterr().out
    with pytest.raises(FileExistsError):
        main()


def test_policy_requires_explicit_distance_and_rejects_invalid_order():
    with pytest.raises(ValidationError):
        SpacePolicy()
    with pytest.raises(ValueError, match="increasing"):
        from daengs_walk.diary.slots.memory import SpaceReplay

        SpaceReplay(
            policy={"park_radius_m": 100},
            batches=[],
            points=[
                {"seq": 2, "at": START, "point": POINT},
                {"seq": 1, "at": START, "point": POINT},
            ],
        )


@pytest.mark.parametrize("crs", ["OGC:CRS84", "EPSG:3857"])
def test_prepared_feature_preserves_original_holes_boundary_and_self_touching_rings(crs):
    import math

    polygons = [
        [ring(-400, -400, 400, 400), ring(20, -10, 40, 10)],
        # Crossed exterior, kept as supplied rather than silently repaired.
        [[ring(500, 0, 600, 100)[i] for i in (0, 2, 1, 3, 0)]],
    ]
    if crs == "EPSG:3857":
        polygons = [
            [
                [
                    [
                        6378137 * math.radians(x),
                        6378137 * math.log(math.tan(math.pi / 4 + math.radians(y) / 2)),
                    ]
                    for x, y in r
                ]
                for r in polygon
            ]
            for polygon in polygons
        ]
    geometry = {"type": "MultiPolygon", "coordinates": polygons}
    prepared = PreparedFeature(geometry, crs, POINT)
    for east in (-401, -400, 0, 20, 30, 40, 400, 401, 500, 525, 550, 600):
        for north in (-400, -10, 0, 10, 25, 50, 75, 100, 400):
            point = moved(east, north)
            assert prepared.covers(point) == feature_contains(geometry, crs, point)


def test_expired_conflict_releases_only_that_subject_and_keeps_valid_new_version():
    old = materials()
    new = materials(parks=[park(1, east=10)])
    result = replay(
        [POINT, POINT, POINT],
        batches=[
            {
                "id": "old",
                "available_seq": 0,
                "materials": old,
                "expires_at": START + timedelta(seconds=2),
            },
            {"id": "new", "available_seq": 1, "materials": new},
        ],
    )
    assert "park" not in loaded(result.frames[1])
    assert "park" in loaded(result.frames[2])
    assert all({"land_cover", "commerce"} <= loaded(f) for f in result.frames)


def test_snapshot_materials_do_not_depend_on_exit_reentry_frequency():
    value = materials()
    points = [POINT, moved(450), POINT, moved(450), POINT]
    snapshots = []
    for interval in (0.02, 60):
        result = replay_spaces(
            {
                "policy": {"park_radius_m": 100},
                "points": [
                    {"seq": i, "at": START + timedelta(seconds=i * interval), "point": p}
                    for i, p in enumerate(points)
                ],
                "batches": [{"id": "saved", "available_seq": 0, "materials": value}],
            }
        )
        snapshots.append([frame.loaded for frame in result.frames])
    assert snapshots[0] == snapshots[1]
    assert [bool(any(r.source == "land_cover" for r in frame)) for frame in snapshots[0]] == [
        True,
        False,
        True,
        False,
        True,
    ]
