"""Normalization survives collection, optional-anchor scenes, stamps and publication."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from daengs_backend.config import settings
from daengs_backend.schemas.walk_diary_slots import SlotPreviewRequest
from daengs_backend.schemas.walk_storyboard import StoryboardRequest
from daengs_backend.services.walk_diary import preview as preview_service
from daengs_backend.services.walk_diary.collection import service as collection
from daengs_backend.services.walk_diary.collection.catalog import normalization_input
from daengs_backend.services.walk_diary.legacy.slots import slot_payload
from daengs_backend.services.walk_diary.lifecycle import generation
from daengs_backend.services.walk_diary.preparation.board import (
    assemble_saved_base_board,
    with_scene_backgrounds,
)
from daengs_backend.services.walk_space_catalog_input import (
    retain_page,
    retained_fields,
)
from daengs_backend.services.walk_storyboard_state import StoryboardConflict
from daengs_walk.diary.contracts.slots import SlotPolicy
from daengs_walk.diary.slots.admission import admit
from daengs_walk.diary.space.materials import AreaInput, normalize_spaces
from tests.walk.diary.test_diary_space_materials import POINT, area, page, park, shop
from tests.walk.support.base_board import policy, saved_case
from tests.walk.support.diary_generation import PATH, body
from tests.walk.support.photo_input import OWNER, WALK


def public_response(request):
    if request.url.host == "api.mcee.go.kr":
        xmin, ymin, xmax, ymax = map(float, request.url.params["BBOX"].split(","))
        ring = [[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax], [xmin, ymin]]
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "crs": {"properties": {"name": "EPSG:3857"}},
                "features": [
                    {
                        "id": "land-1",
                        "properties": {"l3_code": "131"},
                        "geometry": {"type": "Polygon", "coordinates": [ring]},
                    }
                ],
            },
        )
    assert "river" not in str(request.url)
    point = {"lat": float(request.url.params["cy"]), "lng": float(request.url.params["cx"])}
    return httpx.Response(200, json=page([shop(i, point=point, east=i) for i in range(12)]))


@pytest.fixture
def public_collector(monkeypatch):
    def cached(kind, point, radius, path):
        if kind == "commerce":
            raise ValueError("no covering catalog")
        row = {**park(1), "latitude": point["lat"], "longitude": point["lng"], "parkAr": "4500"}
        from datetime import UTC, datetime

        return AreaInput(
            query_point=point, radius_m=radius, pages=(page([row], "park"),)
        ), datetime(2026, 9, 1, tzinfo=UTC)

    monkeypatch.setattr(collection, "cached_area", cached)

    async def collect(board):
        return await collection.collect_spaces(
            board,
            commerce_key="test-only",
            transport=httpx.MockTransport(public_response),
        )

    return collect


@pytest.mark.parametrize("route_patterns", [False, True])
async def test_same_collected_snapshot_has_same_preview_and_generation_stamps(
    monkeypatch, public_collector, route_patterns
):
    monkeypatch.setattr(settings, "walk_diary_route_patterns_enabled", route_patterns)
    assembled, _, _ = saved_case()
    base = assemble_saved_base_board(assembled, policy(3))
    collected = await public_collector(base.board)
    bound = with_scene_backgrounds(base, collected)
    assert any(scene.core.kind == "route_checkpoint" for scene in bound.board.scenes)
    for stamp in bound.slots.stamps:
        normalized = [e for e in stamp.evidence if e.facts.get("format") == "space-material-v1"]
        assert {e.facts["source"] for e in normalized} == {"commerce", "park", "land_cover"}
        assert not any(d.admission == "conflict" for d in stamp.decisions)
    monkeypatch.setattr(preview_service, "read_input", AsyncMock(return_value=assembled))
    response = await preview_service.preview_saved_slots(
        AsyncMock(),
        None,
        None,
        SlotPreviewRequest(target_scene_count=3, collect_backgrounds=True),
        collector=AsyncMock(return_value=collected),
    )
    assert response.preview.revision == bound.slots.revision()
    assert response.preview.stamps == bound.slots.stamps
    if route_patterns:
        # Route patterns are folded into movement claims when movement is configured (#508).
        assert any(
            e.facts.get("format") == "diary-movement-material-v1"
            for stamp in bound.slots.stamps
            for e in stamp.evidence
        )
    payload = slot_payload(bound.board, bound.slots)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "음식점·카페 중심" in serialized
    assert "registered_distribution_in_query_circle" in serialized
    for hidden in ('"source_ids"', '"geometry"', '"registered_count"', '"scene_sequence"'):
        assert hidden not in serialized
    assert "radius_m" in serialized and "nearest_registered_point_m" in serialized
    assert assembled.source.revision() == base.input.source.revision()


def test_catalog_adapter_preserves_classification_scope_completeness_and_conflicts():
    rows = [shop(i) for i in range(12)]
    raw = page(rows)
    retained = retain_page(raw, "commerce")
    catalog = {"area": {"center": POINT, "radius_m": 1200}, **retained_fields([retained])}
    adapted = normalization_input(catalog, "commerce", POINT, 500)
    assert normalize_spaces({"point": POINT, "commerce": adapted}) == normalize_spaces(
        {"point": POINT, "commerce": area(rows)}
    )
    parks = [park(1), {**park(1), "referenceDate": "2026-09-01"}, {**park(2), "parkAr": "4000"}]
    retained = retain_page(page(parks, "park"), "park")
    adapted = normalization_input(retained_fields([retained]), "park", POINT, 500)
    assert normalize_spaces({"point": POINT, "park": adapted}) == normalize_spaces(
        {"point": POINT, "park": area(parks, "park")}
    )
    with pytest.raises(ValueError):
        normalization_input(catalog, "commerce", POINT, 1500)
    catalog["normalization_pages"][0]["body"]["items"].pop()
    with pytest.raises(ValueError, match="hash"):
        normalization_input(catalog, "commerce", POINT, 500)


async def test_failed_land_cover_does_not_remove_other_sources(monkeypatch, public_collector):
    assembled, _, _ = saved_case()
    base = assemble_saved_base_board(assembled, policy(3))

    def response(request):
        return (
            httpx.Response(503)
            if request.url.host == "api.mcee.go.kr"
            else public_response(request)
        )

    collected = await collection.collect_spaces(
        base.board, commerce_key="test-only", transport=httpx.MockTransport(response)
    )
    bound = with_scene_backgrounds(base, collected)
    for stamp in bound.slots.stamps:
        assert {e.facts["source"] for e in stamp.evidence if "source" in e.facts} == {
            "commerce",
            "park",
        }
        assert any(d.reason == "source_unavailable" for d in stamp.decisions)


async def test_collection_timeout_keeps_finished_sources(monkeypatch, public_collector):
    assembled, _, _ = saved_case()
    base = assemble_saved_base_board(assembled, policy(1))

    async def response(request):
        if request.url.host == "api.mcee.go.kr":
            await asyncio.sleep(2)
        return public_response(request)

    collected = await collection.collect_spaces(
        base.board, commerce_key="test-only", transport=httpx.MockTransport(response), timeout_s=0.3
    )
    assert any(s.reason == "collection_timeout" for s in collected.backgrounds)
    assert any(s.status == "known" and s.provider.endswith("park") for s in collected.backgrounds)


async def test_legacy_generation_persists_normalization_and_reopens_without_collection(
    api, monkeypatch, public_collector
):
    client, state, db = api
    monkeypatch.setattr(settings, "walk_diary_space_enabled", True)

    async def collect(board):
        assert db.commit.await_count >= 1
        assert state.row is None  # Acquisition precedes the generation reservation.
        return await public_collector(board)

    spy = AsyncMock(side_effect=collect)
    request = body(state, bundle_format="walk-diary-board-v1")
    response = await generation.generate_diary(
        db,
        OWNER,
        WALK,
        StoryboardRequest.model_validate(request),
        writer=state.writer,
        legacy_collector=spy,
    )
    result = response.model_dump(mode="json")
    assert result["status"] == "ready" and result["bundle"]["model_status"] == "accepted"
    assert state.row.bundle["scene_backgrounds"]
    receipt = state.row.bundle["writing_receipt"]
    assert any(
        e["facts"].get("format") == "space-material-v1"
        for s in receipt["scenes"]
        for e in s["evidence"]
    )
    query = "?bundle_format=walk-diary-board-v1&target_scene_count=3"
    assert client.get(PATH + query).json() == result
    assert client.post(PATH, json=request).json() == result
    spy.assert_awaited_once()


async def test_source_edit_during_legacy_collection_does_not_reserve_or_publish(
    api, monkeypatch, public_collector
):
    _, state, db = api
    monkeypatch.setattr(settings, "walk_diary_space_enabled", True)

    async def collect(board):
        snapshot = await public_collector(board)
        state.entries[0].revision += 1
        return snapshot

    with pytest.raises(StoryboardConflict):
        await generation.generate_diary(
            db,
            OWNER,
            WALK,
            StoryboardRequest.model_validate(body(state, bundle_format="walk-diary-board-v1")),
            writer=state.writer,
            legacy_collector=collect,
        )
    assert state.row is None
    state.writer.assert_not_awaited()


async def test_unknown_spatial_role_is_explicitly_excluded(public_collector):
    assembled, _, _ = saved_case()
    base = assemble_saved_base_board(assembled, policy(3))
    bound = with_scene_backgrounds(base, await public_collector(base.board))
    # Movement evidence is admitted first (#508); the role check applies to space evidence.
    space = next(e for e in bound.slots.stamps[0].evidence if e.part == "space")
    item = space.model_copy(update={"role": "unwired_role"})
    stamp = admit("scene", [item], [], SlotPolicy())
    assert not stamp.evidence
    assert stamp.decisions[0].reason == "unsupported_spatial_relation"
