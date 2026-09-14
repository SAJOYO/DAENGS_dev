"""Fixed cores, real provider-shaped responses, existing admission and writer boundaries."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from daengs_backend.schemas.walk_diary_slots import SlotPreviewRequest
from daengs_backend.services.walk_diary import preview as service
from daengs_backend.services.walk_diary.collection.comparison import collect_scene_backgrounds
from daengs_backend.services.walk_diary.legacy.slots import slot_payload, write_slot_stamps
from daengs_evals.diary_slots_demo import demo_input
from daengs_walk.diary.board.backgrounds import SceneBackgroundSnapshot
from daengs_walk.diary.board.preview import prepare_slot_preview
from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.contracts.slots import SlotPolicy
from daengs_walk.diary.slots.service import prepare_board_slots
from daengs_walk.diary.space.kakao import project_kakao_background
from tests.walk.support.base_board import policy, saved_case


def empty_place_then_kakao(request):
    if request.url.host == "places.test":
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"groups": []})
    assert request.headers["authorization"] == "KakaoAK test-only-key"
    if "coord2address" in request.url.path:
        return httpx.Response(
            200,
            json={
                "documents": [
                    {
                        "address": {
                            "region_1depth_name": "서울",
                            "region_2depth_name": "강남구",
                            "region_3depth_name": "역삼동",
                        }
                    }
                ]
            },
        )
    if request.url.params.get("category_group_code") != "CE7":
        return httpx.Response(200, json={"documents": []})
    row = {
        "id": "cafe-1",
        "place_name": "테스트 커피",
        "distance": "40",
        "category_group_code": "CE7",
        "category_name": "음식점 > 카페",
    }
    return httpx.Response(
        200,
        json={
            "documents": [
                row,
                row,
                {**row, "id": "outside", "distance": "999"},
                {**row, "id": "wrong-category", "category_group_code": "FD6"},
            ]
        },
    )


async def collected_case():
    assembled, route, _ = saved_case()
    preview = prepare_slot_preview(assembled.source, SlotPolicy(), policy(), route=route)
    collected = await collect_scene_backgrounds(
        preview.base_board,
        place_api="https://places.test",
        kakao_key="test-only-key",
        transport=httpx.MockTransport(empty_place_then_kakao),
    )
    return assembled, route, preview, collected


@pytest.mark.parametrize("address", [[], "invalid", {"documents": [None]}])
async def test_malformed_provider_address_is_unknown_instead_of_breaking_the_comparison(address):
    _assembled, _route, preview, collected = await collected_case()
    saved = next(s for s in collected.backgrounds if s.provider == "kakao-local")
    payload = {**saved.payload, "address_response": address}
    saved = saved.model_copy(update={"payload": payload, "payload_sha256": digest(payload)})
    core = next(s.core for s in preview.base_board.scenes if s.core_ref == saved.target)
    assert project_kakao_background(saved, core).reason == "invalid_kakao_payload"


async def test_checkpoint_collection_uses_common_admission_without_changing_source_or_scenes():
    assembled, route, preview, collected = await collected_case()
    before = assembled.source.revision(), preview.base_board.model_dump(mode="json")
    slots = prepare_board_slots(
        assembled.source, preview.base_board, SlotPolicy(), route=route, scene_backgrounds=collected
    )
    for scene, stamp in zip(preview.base_board.scenes, slots.stamps, strict=True):
        assert stamp.scene_id == scene.id
        if scene.core.kind == "route_checkpoint":
            assert len(stamp.evidence) == 1
            assert stamp.evidence[0].facts["name"] == "테스트 커피"
            assert stamp.location_reference.facts["address_type"] == "legal_dong"
            assert any(d.admission == "duplicate" for d in stamp.decisions)
            assert any(d.reason.startswith("invalid_provider_row") for d in stamp.decisions)
    assert before == (assembled.source.revision(), preview.base_board.model_dump(mode="json"))
    narrowed = prepare_board_slots(
        assembled.source,
        preview.base_board,
        SlotPolicy(space_radius_m=30),
        route=route,
        scene_backgrounds=collected,
    )
    assert all(not s.evidence for s in narrowed.stamps)
    assert all(s.location_reference is not None for s in narrowed.stamps)


@pytest.mark.parametrize(
    "change", ["board", "target_time", "target_version", "query_point", "payload"]
)
async def test_collected_evidence_cannot_migrate_to_another_core_or_changed_payload(change):
    assembled, route, preview, collected = await collected_case()
    raw = collected.model_dump(mode="json")
    if change == "board":
        raw["board_revision"] = "f" * 64
    elif change == "target_time":
        raw["targets"][0]["anchor"]["event_at"] = "2030-01-01T00:00:00Z"
    elif change == "target_version":
        raw["backgrounds"][0]["target"]["version"] = "f" * 64
    elif change == "query_point":
        raw["backgrounds"][0]["query_point"]["lat"] += 0.001
    else:
        raw["backgrounds"][1]["payload"]["radius_m"] = 1000
    with pytest.raises(ValueError):
        changed = SceneBackgroundSnapshot.model_validate(raw)
        prepare_board_slots(
            assembled.source,
            preview.base_board,
            SlotPolicy(),
            route=route,
            scene_backgrounds=changed,
        )


async def test_missing_location_and_provider_failure_keep_the_original_board():
    assembled, route, preview, _ = await collected_case()
    failed = await collect_scene_backgrounds(
        preview.base_board,
        place_api="https://places.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
    )
    slots = prepare_board_slots(
        assembled.source, preview.base_board, SlotPolicy(), route=route, scene_backgrounds=failed
    )
    writer = AsyncMock()
    result = await write_slot_stamps(preview.base_board, slots, writer)
    assert result.model_status == "not_requested"
    writer.assert_not_awaited()
    unlocated = prepare_slot_preview(assembled.source, SlotPolicy(), policy(), route=None)
    calls = []
    await collect_scene_backgrounds(
        unlocated.base_board,
        place_api="https://places.test",
        transport=httpx.MockTransport(lambda request: calls.append(request)),
    )
    assert not calls


def test_writer_keeps_original_and_local_materials_without_global_scene_sequence():
    source, route, _ = demo_input()
    preview = prepare_slot_preview(source, SlotPolicy(), policy(3), route=route)
    slots = prepare_board_slots(source, preview.base_board, SlotPolicy(), route=route)
    payload = slot_payload(preview.base_board, slots)
    assert "scene_sequence" not in payload
    for item in payload["scenes"]:
        assert "original" not in item
        assert "center" not in item and "anchor" not in item
        assert any(item["scene"].values())
    assert source.owner_id not in json.dumps(payload)


async def test_explicit_collection_runs_after_owner_read_lock_and_never_reserves_publication(
    monkeypatch,
):
    assembled, _route, preview, collected = await collected_case()
    monkeypatch.setattr(service, "read_input", AsyncMock(return_value=assembled))
    db = type("DB", (), {"commit": AsyncMock(), "rollback": AsyncMock()})()

    async def collect(board):
        db.commit.assert_awaited_once()
        assert board == preview.base_board
        return collected

    writer = AsyncMock(side_effect=lambda p: p)
    response = await service.preview_saved_slots(
        db,
        "owner",
        "walk",
        SlotPreviewRequest(target_scene_count=5, collect_backgrounds=True),
        writer=writer,
        collector=collect,
    )
    assert response.preview.base_board == preview.base_board
    assert all(s.evidence for s in response.preview.stamps)
    writer.assert_awaited_once()
    db.rollback.assert_not_awaited()
