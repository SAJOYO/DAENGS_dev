"""Public evidence reaches the existing stamps/writer without widening its facts."""

import json
from unittest.mock import AsyncMock

import pytest

from daengs_backend.services.walk_diary_writing import write_diary
from daengs_walk.diary_background import project_background
from daengs_walk.diary_input import SavedBackground, digest, material_ref
from daengs_walk.diary_stamps import StampPolicy, prepare_stamps
from daengs_walk.diary_writing import prepare_writing
from tests.walk.support.diary import record, with_backgrounds


def public(core, provider, **changes):
    payload = (
        {
            "format": "sgis-dong-v1",
            "address_type": "administrative_dong",
            "address": {
                "sido_nm": "서울특별시",
                "sgg_nm": "강남구",
                "emdong_nm": "역삼1동",
                "sido_cd": "11",
                "sgg_cd": "230",
                "emdong_cd": "610",
            },
        }
        if provider == "sgis"
        else {
            "format": "public-park-nearby-v1",
            "geometry": "registered_point",
            "radius_m": 250,
            "visit_confirmed": False,
            "coverage": "national_catalog_registered_points",
            "items": [
                {
                    "parkNm": "합성 공원",
                    "manageNo": "public-park-1",
                    "parkSe": "근린공원",
                    "latitude": 37.5,
                    "longitude": 127.001,
                    "distance_m": 88.2,
                    "referenceDate": "2026-09-01",
                }
            ],
        }
    )
    payload.update(
        query_point=core.anchor.point.model_dump(mode="json"), location_basis="original_location"
    )
    payload.update(changes)
    return SavedBackground(
        id=provider,
        target=material_ref(core),
        provider=provider,
        payload_schema="walk-entry-context-v1",
        policy_version="walk-entry-context-v1",
        query_point=core.anchor.point,
        tags=("space",),
        status="known",
        retrieved_at="2026-09-09T02:00:00Z",
        temporal_basis="lookup_snapshot",
        payload=payload,
        payload_sha256=digest(payload),
    )


async def test_dong_and_park_fill_separate_existing_slots_and_leave_user_text_untouched():
    core = record()
    backgrounds = [public(core, p) for p in ["sgis", "data-go-kr-parks"]]
    source = with_backgrounds(core, backgrounds=backgrounds)
    prepared = prepare_stamps(source, StampPolicy(target_scene_count=3))
    pieces = prepared.plan.scenes[0].background
    assert [p.kind for p in pieces] == ["place_reference", "space_relation"]
    assert pieces[0].facts["dong"] == "역삼1동"
    assert pieces[0].facts["source_ref"]["ref"] == "11:230:610"
    request = prepare_writing(source, prepared)
    assert request.payload["title_context"][0]["place_context"][0]["dong"] == "역삼1동"
    assert list(request.payload["background_dictionary"]) == ["e1"]
    park = request.payload["background_dictionary"]["e1"]
    assert park["reference"] == "registered_park_point"
    assert park["relation"] == "distance_only_not_entry_or_visit"
    assert park["reference_date"] == "2026-09-01"
    assert all(
        key not in json.dumps(request.payload) for key in ['"latitude"', '"lat"', '"source_ref"']
    )
    call = AsyncMock(
        return_value={
            "title": "공원 가까이 남긴 기록",
            "scenes": [
                {"scene_id": "s1", "text": "공원이 가까이에 있었다.", "evidence_ids": ["e1"]}
            ],
        }
    )
    output = await write_diary(source, prepared, call)
    assert (
        output.model_status == "accepted" and output.scenes[0].user_record.text == core.content.text
    )
    assert len(output.scenes) == 1  # No invented actions to fill three scenes.


@pytest.mark.parametrize(
    "provider,change",
    [
        ("sgis", {"address_type": "legal_dong"}),
        ("sgis", {"query_point": {"lat": 37.5001, "lng": 127}}),
        ("sgis", {"location_basis": "interpolated"}),
        ("data-go-kr-parks", {"geometry": "polygon"}),
        ("data-go-kr-parks", {"visit_confirmed": True}),
    ],
)
def test_mismatched_public_contract_is_rejected(provider, change):
    core = record()
    assert not project_background(public(core, provider, **change), core).pieces


def test_park_distance_is_recomputed_before_admission():
    core = record()
    saved = public(core, "data-go-kr-parks")
    saved.payload["items"][0]["distance_m"] = 5
    projection = project_background(saved, core)
    assert not projection.pieces and projection.rejected_rows == (0,)


async def test_dong_only_is_display_metadata_and_does_not_trigger_a_model_call():
    core = record()
    source = with_backgrounds(core, backgrounds=[public(core, "sgis")])
    prepared = prepare_stamps(source, StampPolicy(target_scene_count=1))
    call = AsyncMock()
    output = await write_diary(source, prepared, call)
    assert output.model_status == "not_requested"
    assert output.scenes[0].place_reference[0].facts["dong"] == "역삼1동"
    call.assert_not_awaited()
