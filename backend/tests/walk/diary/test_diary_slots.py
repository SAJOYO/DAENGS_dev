"""Cross-part applicability and prose boundary, using real canonical motion."""

from copy import deepcopy
from datetime import timedelta

import pytest

from daengs_backend.services.walk_diary.legacy.slots import write_slot_preview, writing_payload
from daengs_evals.diary_slots_demo import demo_input
from daengs_walk.diary.board.models import BaseBoardPolicy
from daengs_walk.diary.board.preview import prepare_slot_preview
from daengs_walk.diary.board.scene_input import scene_materials
from daengs_walk.diary.contracts.input import DiaryInput, digest
from daengs_walk.diary.contracts.slots import SlotPolicy
from daengs_walk.diary.selection.stamps import StampPolicy


def prepare(source=None, route=None, **policy):
    if source is None:
        source, route, _ = demo_input()
    return prepare_slot_preview(
        source,
        SlotPolicy(**policy),
        BaseBoardPolicy(intermediate=StampPolicy(target_scene_count=3)),
        route=route,
    )


def test_all_parts_and_user_records_survive():
    preview = prepare()
    assert len(preview.scenes) == 5  # Three original records plus start/end.
    for stamp in preview.stamps[1:-1]:
        assert [e.part for e in stamp.evidence].count("space") == 3
        assert {e.part for e in stamp.evidence} == {"space", "environment", "motion"}
        assert any(d.admission == "duplicate" for d in stamp.decisions)
    assert preview.stamps[-2].evidence[2].role == "before_scene_motion"
    assert preview.scenes[1].body == "  벤치 옆에서 물을 마셨다.\n"


def test_capacity_is_separate_from_applicability_and_empty_is_valid():
    limited = prepare(space_radius_m=60, space_slots=1, total_slots=2)
    stamp = limited.stamps[1]
    assert len(stamp.evidence) == 2
    assert any(
        d.reason == "outside_space_radius" and d.eligibility == "fail" for d in stamp.decisions
    )
    assert any(d.admission == "total_capacity" and d.eligibility == "pass" for d in stamp.decisions)
    empty = prepare(space_slots=0, environment_slots=0, motion_slots=0)
    assert not any(s.evidence for s in empty.stamps)
    assert empty.base_board.scenes == limited.base_board.scenes


@pytest.mark.parametrize(
    "change,reason",
    [
        ("time", "outside_weather_interval"),
        ("area", "outside_weather_area"),
    ],
)
def test_weather_requires_scene_time_and_area(change, reason):
    source, route, _ = demo_input()
    raw = source.model_dump(mode="json")
    saved = next(b for b in raw["backgrounds"] if b["id"] == "environment-1")
    if change == "time":
        saved["payload"]["valid_until"] = (source.started_at + timedelta(seconds=30)).isoformat()
    else:
        saved["payload"]["area_center"]["lat"] = 38
    saved["payload_sha256"] = digest(saved["payload"])
    preview = prepare(DiaryInput.model_validate(raw), route)
    assert not any(e.part == "environment" for e in preview.stamps[1].evidence)
    assert any(d.reason == reason for d in preview.stamps[1].decisions)


def test_no_route_means_no_motion_and_no_other_scene_background():
    source, _, _ = demo_input()
    raw = source.model_dump(mode="json")
    raw["selected_background_ids"] = ["space-1"]
    preview = prepare(DiaryInput.model_validate(raw))
    assert all(e.part == "space" for s in preview.stamps for e in s.evidence)
    assert preview.stamps[1].evidence
    assert all(not s.evidence for s in preview.stamps[2:])


def test_motion_cannot_bridge_a_removed_canonical_segment():
    from daengs_walk.diary.slots.sources import candidates_for_scene, verified_motion

    source, route, _ = demo_input()
    preview = prepare(source, route)
    motion, blocks = verified_motion(source, route)
    # The record is after the fast interval. Removing the connecting edge makes
    # distinct blocks even though both still have chain_index=0.
    nodes = next(iter(blocks.values()))
    split = next(i for i, n in enumerate(nodes) if n["observation"]["client_seq"] == 28)
    broken = {0: nodes[:split], 1: nodes[split:]}
    candidates, decisions = candidates_for_scene(
        source,
        preview.scenes[-2],
        SlotPolicy(),
        motion,
        broken,
    )
    assert not any(e.part == "motion" for e in candidates)
    assert any(d.reason == "canonical_continuity_break" for d in decisions)


async def test_writer_keeps_originals_and_never_changes_slots():
    preview = prepare()

    async def generate(payload, schema):
        return {
            "scenes": [
                {
                    "scene_id": s["scene_id"],
                    "text": "주변에 공원이 있었다.",
                    "evidence_ids": [scene_materials(s)[0]["id"]],
                    "action_id": s["action"]["id"] if s["action"] else None,
                }
                for s in payload["scenes"]
            ]
        }

    result = await write_slot_preview(preview, generate)
    assert result.model_status == "accepted"
    assert result.stamps == preview.stamps
    for original, written in zip(preview.scenes, result.scenes, strict=True):
        assert written.body.endswith(original.body)
        assert (written.anchor, written.core) == (original.anchor, original.core)


async def test_cross_scene_citation_and_provider_failure_keep_base_board():
    preview = prepare()
    payload = writing_payload(preview)
    foreign = scene_materials(payload["scenes"][1])[0]["id"]

    async def invalid(*args):
        return {
            "scenes": [
                {
                    "scene_id": s["scene_id"],
                    "text": "공원 주변이었다.",
                    "evidence_ids": [foreign],
                    "action_id": s["action"]["id"] if s["action"] else None,
                }
                for s in payload["scenes"]
            ]
        }

    result = await write_slot_preview(preview, invalid)
    assert result.failure_code == "invalid_response"
    assert result.scenes == preview.base_board.scenes

    async def failed(*args):
        raise RuntimeError("do not expose provider errors")

    result = await write_slot_preview(preview, failed)
    assert result.failure_code == "provider_failed"
    assert result.scenes == preview.base_board.scenes


def test_input_order_does_not_change_slot_plan():
    source, route, _ = demo_input()
    raw = deepcopy(source.model_dump(mode="json"))
    for key in ("records", "backgrounds", "observations", "selected_background_ids"):
        raw[key].reverse()
    assert prepare(source, route) == prepare(DiaryInput.model_validate(raw), route)
