"""Prepared snapshot -> actual writer request -> parsed answer and receipt."""

import json
from copy import deepcopy

import pytest

from daengs_backend.services.walk_diary.writing.relational import (
    validate_prepared,
    write_relational_diary,
)
from daengs_backend.services.walk_diary.writing.short_memory import write_with_short_memory
from daengs_walk.diary.relational.planning import make_plan
from daengs_walk.diary.relational.relations.registry import collect_spatial_comparisons
from daengs_walk.value_contracts import digest
from tests.walk.diary.test_scene_snapshot_assembly import prepared, public_collector  # noqa: F401


def response(request):
    return json.dumps(
        {
            "focus": "현재 배경과 공간 관계",
            "text": "공원 가까이에 길이 있었다.",
            "relation_ids": request["relation_ids"][:1],
            "evidence_ids": [request["current"]["facts"][0]["id"]],
        },
        ensure_ascii=False,
    )


async def test_real_writer_receives_whole_snapshots_and_separate_citation_ids(prepared):  # noqa: F811
    seen = []

    async def send(stage, request, schema):
        assert stage == "space"
        seen.append(request)
        assert request["version"] == "scene-comparison-v1"
        assert "focus" in schema["required"] and "relation_ids" in schema["required"]
        assert schema["properties"]["evidence_ids"]["items"]["enum"] == request["citation_ids"]
        assert all(k.startswith("e") and k[1:].isdigit() for k in request["citation_ids"])
        assert "source_refs" not in json.dumps(request)
        assert "card_header" not in request and "short_memory" not in request
        assert "required_relation_ids" not in request
        return response(request)

    result = await write_with_short_memory(prepared, send=send, review=False)
    assert [r["current"]["scene_id"] for r in seen] == [
        f["scene_id"] for f in prepared["snapshot"]["frames"]
    ]
    assert seen[0]["earlier"] is None
    assert seen[1]["earlier"]["scene_id"] == seen[0]["current"]["scene_id"]
    assert seen[1]["connection"]["elapsed_seconds"] > 0
    assert seen[1]["route_evidence"] is not None
    assert seen[1]["route_evidence"]["id"] in seen[1]["citation_ids"]
    assert all(
        r["status"] == "returned" and r["answer"]["focus"]
        for r in result["receipt"]["writing"]["results"]
    )


async def test_old_payload_cannot_replace_comparison_request(prepared):  # noqa: F811
    changed = deepcopy(prepared)
    task = changed["snapshot"]["plans"][1]["space_task"]
    task["payload"]["earlier"]["facts"] = []
    task["revision"] = digest([task["stage"], task["scene_id"], task["payload"]])
    plan = changed["snapshot"]["plans"][1]
    plan["revision"] = digest({k: v for k, v in plan.items() if k != "revision"})
    changed["revision"] = digest(changed["snapshot"])
    with pytest.raises(ValueError):
        validate_prepared(changed)


async def test_unknown_relation_is_rejected_by_response_parser(prepared):  # noqa: F811
    async def send(stage, request, schema):
        answer = json.loads(response(request))
        answer["relation_ids"] = ["invented-relation"]
        return json.dumps(answer)

    written = await write_relational_diary(prepared, send=send, review=False)
    assert all(r["failure_phase"] == "references" for r in written["results"])


async def test_park_change_opens_task_without_point_background_change(prepared):  # noqa: F811
    a, b = deepcopy(prepared["snapshot"]["frames"][:2])
    # Same raw location/road classification; only registered-point distance differs.
    for frame in (a, b):
        snap = frame["scene_snapshot"]
        snap["facts"] = [f for f in snap["facts"] if f["family"] == "surrounding_object"]
    left, right = a["scene_snapshot"]["facts"][0], b["scene_snapshot"]["facts"][0]
    right["value"]["registered_point"] = deepcopy(left["value"]["registered_point"])
    right["value"]["distance_m"] = left["value"]["distance_m"] + 50
    b["spatial_comparison_slots"] = collect_spatial_comparisons(
        b["scene_snapshot"], a["scene_snapshot"]
    )
    plan = make_plan(b, a, None)
    assert plan["space_task"]["payload"]["version"] == "scene-comparison-v1"
    assert (
        plan["space_task"]["payload"]["relation_slots"]["proximity"]["items"][0]["distance_delta_m"]
        == 50
    )
    assert plan["relation_selection"]["space"] == []


@pytest.mark.parametrize("prior_only_recovery", [False, True])
async def test_failed_intro_recovers_once_at_next_same_context(prepared, prior_only_recovery):  # noqa: F811
    from daengs_backend.services.walk_diary.preparation.scene_snapshot import (
        assemble_scene_snapshot,
    )

    snapshot = deepcopy(prepared["snapshot"])
    snapshot["plans"] = []
    previous = state = None
    for frame in snapshot["frames"]:
        frame["eligible_evidence"] = [
            e for e in frame["eligible_evidence"] if e["facts"].get("source") == "land_cover"
        ]
        scene, header = assemble_scene_snapshot(
            frame,
            backgrounds=[snapshot["scene_backgrounds"][k] for k in frame["scene_background_ids"]],
            road_snapshots=snapshot["road_snapshots"],
        )
        frame["scene_snapshot"], frame["card_header"] = (
            scene.model_dump(mode="json"),
            header.model_dump(mode="json"),
        )
        frame["spatial_comparison_slots"] = collect_spatial_comparisons(
            scene, previous["scene_snapshot"] if previous else None
        )
        plan = make_plan(frame, previous, None, state)
        snapshot["plans"].append(plan)
        previous, state = frame, plan["state_after"]
    assert sum(p["space_task"] is not None for p in snapshot["plans"]) == 1
    calls = []

    async def send(stage, request, schema):
        calls.append(request["current"]["scene_id"])
        if len(calls) == 1:
            raise TimeoutError("intro unavailable")
        if prior_only_recovery and len(calls) == 2:
            return json.dumps(
                {
                    "focus": "이전 장소만 소개",
                    "text": "앞서 남긴 곳에는 길이 있었다.",
                    "evidence_ids": [request["earlier"]["facts"][0]["id"]],
                    "relation_ids": [],
                }
            )
        return response(request)

    result = await write_with_short_memory(
        {"snapshot": snapshot, "revision": digest(snapshot)}, send=send, review=False
    )
    count = 3 if prior_only_recovery else 2
    assert calls == [f["scene_id"] for f in snapshot["frames"][:count]]
    assert result["receipt"]["cards"][count]["parts"]["space"]["status"] == "not_requested"
    if prior_only_recovery:
        publication = result["receipt"]["cards"][1]["comparison"]
        assert publication["selection"] is not None
        assert publication["delivery_after"]["active_introduction"] is None
        assert len(publication["delivery_after"]["recent_deliveries"]) == 1
