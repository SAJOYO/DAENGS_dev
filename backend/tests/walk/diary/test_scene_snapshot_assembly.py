"""Exercise actual normalization/eligibility/preparation, without external requests."""

from copy import deepcopy
from dataclasses import replace

import pytest

from daengs_backend.services.walk_diary.preparation.board import (
    assemble_saved_base_board,
    with_scene_backgrounds,
)
from daengs_backend.services.walk_diary.preparation.relational import prepare_relational_diary
from daengs_backend.services.walk_diary.preparation.scene_snapshot import assemble_scene_snapshot
from daengs_walk.diary.contracts.slots import SlotPolicy
from daengs_walk.diary.relational.scene_comparison_contracts import SceneSnapshot
from daengs_walk.value_contracts import digest
from tests.walk.diary.test_diary_space_integration import public_collector  # noqa: F401
from tests.walk.support.base_board import policy, saved_case


@pytest.fixture
async def prepared(public_collector):  # noqa: F811 -- imported pytest fixture
    source, _, _ = saved_case()
    base = assemble_saved_base_board(source, policy(3))
    bound = with_scene_backgrounds(base, await public_collector(base.board))
    bound = replace(
        bound,
        slots=bound.slots.model_copy(
            update={
                "policy": SlotPolicy(space_slots=0, environment_slots=0, total_slots=0),
            }
        ),
    )
    roads = [
        {
            "point": s.anchor.point.model_dump(mode="json"),
            "addr_type": 10,
            "retrieved_at": "2026-09-15T01:00:00Z",
            "response": {"errCd": 0, "result": [{"road_nm": "양재천로3길", "bd_main_nm": "123"}]},
        }
        for s in base.board.scenes
        if s.anchor.point
    ]
    return prepare_relational_diary(bound, road_snapshots=iter(roads))


async def test_zero_budget_still_assembles_all_normalized_families(prepared):
    for index, frame in enumerate(prepared["snapshot"]["frames"]):
        slots = frame["spatial_comparison_slots"]
        assert set(slots) == {"background", "proximity", "area_context"}
        if index == 0:
            assert {s["status"] for s in slots.values()} == {"not_applicable"}
        else:
            assert slots["background"]["items"]
    for frame in prepared["snapshot"]["frames"]:
        snap = SceneSnapshot.model_validate(frame["scene_snapshot"])
        assert {f.family for f in snap.facts} == {
            "road",
            "land_cover",
            "surrounding_object",
            "area_context",
        }
        assert snap.recorded_at.isoformat().replace("+00:00", "Z") == frame["anchor"]["event_at"]
        park = next(f for f in snap.facts if f.family == "surrounding_object")
        assert park.subject_key == "public-normalized-park:1"
        assert park.value["area_m2"] == 4500
        assert park.retrieved_at is not None and park.observed_at is None
        assert park.value["registered_point"] == frame["anchor"]["point"]
        road = next(f for f in snap.facts if f.family == "road")
        assert road.value == {"name": "양재천로3길"}
        assert "bd_main_nm" not in snap.model_dump_json()
        area = next(f for f in snap.facts if f.family == "area_context")
        assert area.value["registered_count"] == 12
        assert area.value["query"]["point"] == frame["anchor"]["point"]
    areas = [
        next(f for f in x["scene_snapshot"]["facts"] if f["family"] == "area_context")
        for x in prepared["snapshot"]["frames"]
    ]
    assert len({x["scope"]["coverage_key"] for x in areas}) == len(areas)


async def test_header_and_originals_are_outside_space_facts(prepared):
    frame = deepcopy(prepared["snapshot"]["frames"][0])
    frame["eligible_evidence"] += [
        {
            "id": "dong",
            "role": "scene_address_reference",
            "source_id": "sgis",
            "facts": {"dong": "양재2동", "full_address": "노출하지 않을 주소"},
        },
        {
            "id": "weather",
            "role": "grid_temperature_observation",
            "source_id": "kma",
            "facts": {"temperature_c": 24.2, "observed_at": "2026-09-15T01:00:00Z"},
        },
    ]
    frame["action"] = {"recorded_action": "현재 행동"}
    frame["note"] = "사용자 메모 원문"
    before = deepcopy(frame)
    snap, header = assemble_scene_snapshot(frame)
    assert frame == before
    assert header.dong == "양재2동"
    assert header.weather["observations"][0]["facts"]["temperature_c"] == 24.2
    for excluded in ("양재2동", "temperature_c", "사용자 메모 원문", "현재 행동", "full_address"):
        assert excluded not in snap.model_dump_json()


async def test_wrong_point_and_conflicting_roads(prepared):
    frame = deepcopy(prepared["snapshot"]["frames"][0])
    other = {"lat": 0, "lng": 0}
    road = {
        "point": frame["anchor"]["point"],
        "addr_type": 10,
        "response": {"errCd": 0, "result": [{"road_nm": "첫길"}]},
    }
    conflict = deepcopy(road)
    conflict["response"]["result"][0]["road_nm"] = "다른길"
    snap, _ = assemble_scene_snapshot(frame, road_snapshots=[road, conflict])
    assert snap.collection["road"] == "partial"
    assert not any(f.family == "road" for f in snap.facts)
    for e in frame["eligible_evidence"]:
        if e["facts"].get("source") == "park":
            e["diagnostics"]["scope"]["query_point"] = other
    with pytest.raises(ValueError, match="different query point"):
        assemble_scene_snapshot(frame)


async def test_missing_evidence_does_not_become_empty_or_not_requested(prepared):
    frame = deepcopy(prepared["snapshot"]["frames"][0])
    frame["eligible_evidence"] = []
    snap, _ = assemble_scene_snapshot(frame)
    assert set(snap.collection.values()) == {"unknown"}
    assert snap.facts == ()  # Never fall back to frame.space.materials.
    source = {
        "id": "missing",
        "provider": "public-normalized-park",
        "status": "empty",
        "payload": None,
        "reason": None,
    }
    snap, _ = assemble_scene_snapshot(frame, backgrounds=[source])
    assert snap.collection["surrounding_object"] == "empty"


async def test_rehashed_comparison_tampering_is_rejected(prepared):
    from daengs_backend.services.walk_diary.writing.relational import validate_prepared

    validate_prepared(prepared)
    changed = deepcopy(prepared)
    changed["snapshot"]["frames"][1]["spatial_comparison_slots"]["background"]["items"][0][
        "result"
    ] = "incomparable"
    changed["revision"] = digest(changed["snapshot"])
    with pytest.raises(ValueError, match="spatial comparison slots"):
        validate_prepared(changed)


async def test_snapshot_and_relations_cannot_be_rewritten_together(prepared):
    from daengs_backend.services.walk_diary.writing.relational import validate_prepared
    from daengs_walk.diary.relational.relations.registry import collect_spatial_comparisons

    changed = deepcopy(prepared)
    frames = changed["snapshot"]["frames"]
    park = next(
        f for f in frames[1]["scene_snapshot"]["facts"] if f["family"] == "surrounding_object"
    )
    park["value"]["distance_m"] = 9876
    for index, frame in enumerate(frames):
        frame["spatial_comparison_slots"] = collect_spatial_comparisons(
            frame["scene_snapshot"], frames[index - 1]["scene_snapshot"] if index else None
        )
    changed["revision"] = digest(changed["snapshot"])
    with pytest.raises(ValueError, match="scene snapshot does not match eligible evidence"):
        validate_prepared(changed)


async def test_rebuild_survives_json_round_trip_and_rejects_lost_manifest(prepared):
    import json

    from daengs_backend.services.walk_diary.writing.relational import validate_prepared

    restored = json.loads(json.dumps(prepared))
    validate_prepared(restored)
    del restored["snapshot"]["scene_backgrounds"]
    restored["revision"] = digest(restored["snapshot"])
    with pytest.raises(ValueError, match="source manifest missing"):
        validate_prepared(restored)


@pytest.mark.parametrize("extra_failure", [False, True])
async def test_complete_empty_commerce_is_not_partial(prepared, extra_failure):
    from daengs_walk.diary.space.materials import normalize_spaces
    from tests.walk.diary.test_diary_space_materials import area

    frame = deepcopy(prepared["snapshot"]["frames"][0])
    point = frame["anchor"]["point"]
    normalized = normalize_spaces({"point": point, "commerce": area([], point=point)})
    payload = normalized.model_dump(mode="json")
    if extra_failure:
        payload["audit"].append({"source": "commerce", "reason": "invalid_or_conflicting_rows"})
    frame["eligible_evidence"] = []
    source = {
        "id": "empty-commerce",
        "provider": "public-normalized-commerce",
        "status": "known",
        "reason": None,
        "payload": payload,
    }
    snapshot, _ = assemble_scene_snapshot(frame, backgrounds=[source])
    assert snapshot.collection["area_context"] == ("partial" if extra_failure else "empty")
    assert "no_registered_shops_in_footprint" in snapshot.collection_reasons["area_context"]


@pytest.mark.parametrize("name", ["매헌로 123", "서울특별시 서초구 매헌로", "null"])
async def test_full_address_never_used_as_road_name(prepared, name):
    frame = prepared["snapshot"]["frames"][0]
    road = {
        "point": frame["anchor"]["point"],
        "addr_type": 10,
        "response": {"errCd": 0, "result": [{"road_nm": name}]},
    }
    snap, _ = assemble_scene_snapshot(frame, road_snapshots=[road])
    assert not any(f.family == "road" for f in snap.facts)
