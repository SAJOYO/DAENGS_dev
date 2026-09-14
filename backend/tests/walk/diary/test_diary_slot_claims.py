"""Mixed real spatial projectors and conflicting same-entity claims in the preview."""

from datetime import timedelta
from runpy import run_path

import pytest

from daengs_backend.services.walk_diary.legacy.slots import write_slot_preview, writing_payload
from daengs_backend.services.walk_public_context import collect_public
from daengs_walk.diary.board.models import BaseBoardPolicy
from daengs_walk.diary.board.preview import prepare_slot_preview
from daengs_walk.diary.contracts.slots import SlotPolicy
from daengs_walk.diary.selection.stamps import StampPolicy
from daengs_walk.diary.slots.admission import admit
from daengs_walk.diary.slots.sources import evidence
from daengs_walk.diary.slots.spatial import spatial_claim
from daengs_walk.diary.space.projection import project_background
from tests.walk.context.test_walk_area_context import POINT, enabled, saved
from tests.walk.diary.test_diary_public_background import public
from tests.walk.support.diary import nearby, place_payload, record, with_backgrounds


def preview(core, backgrounds, **policy):
    return prepare_slot_preview(
        with_backgrounds(core, backgrounds=backgrounds),
        SlotPolicy(**policy),
        BaseBoardPolicy(intermediate=StampPolicy(target_scene_count=1)),
    )


async def test_point_geometry_area_and_address_do_not_share_a_numeric_rank(tmp_path, monkeypatch):
    await enabled(tmp_path, monkeypatch)
    core = record()
    backgrounds = [public(core, "sgis"), public(core, "data-go-kr-parks")]
    backgrounds += [
        saved(core, await collect_public(tag, POINT)) for tag in ("space.river", "space.commerce")
    ]
    result = preview(core, backgrounds, space_slots=3)
    stamp = result.stamps[1]
    assert [e.role for e in stamp.evidence] == [
        "scene_registered_point_distance",
        "scene_geometry_distance",
        "scene_area_context",
    ]
    assert [e.rank for e in stamp.evidence] == [(88.2,), (50.0,), ()]
    assert stamp.location_reference.role == "scene_address_reference"
    assert stamp.location_reference.rank == ()
    single = preview(core, backgrounds, space_slots=1, total_slots=1).stamps[1]
    assert single.evidence[0].role == "scene_registered_point_distance"
    assert single.location_reference is not None
    assert len(writing_payload(result)["scenes"][0]["scene"]["where"]) == 4


async def test_location_reference_can_be_cited_and_disabled():
    core = record()
    backgrounds = [public(core, "sgis")]
    result = preview(core, backgrounds, space_slots=0)
    stamp = result.stamps[1]
    assert not stamp.evidence and stamp.location_reference is not None

    async def generate(payload, schema):
        return {
            "scenes": [
                {
                    "scene_id": stamp.scene_id,
                    "text": "역삼1동에서 남긴 기록이다.",
                    "evidence_ids": [stamp.location_reference.id],
                    "action_id": None,
                }
            ]
        }

    written = await write_slot_preview(result, generate)
    assert written.model_status == "accepted"
    assert written.scenes[1].body.endswith(core.content.text)
    assert not preview(core, backgrounds, include_location_reference=False).stamps[1].materials()


@pytest.mark.parametrize("radius", [60, 250])
def test_conflicting_values_are_not_chosen_by_distance_recency_or_order(radius):
    core = record()
    old = nearby(core, "old", payload=place_payload(("park", "공원", 40)))
    newer = nearby(core, "new", payload=place_payload(("park", "공원", 200))).model_copy(
        update={"retrieved_at": old.retrieved_at + timedelta(hours=1)}
    )
    result = preview(core, [old, newer], space_radius_m=radius)
    stamp = result.stamps[1]
    assert not stamp.evidence
    assert len([d for d in stamp.decisions if d.admission == "conflict"]) == 2
    assert {c["facts"]["distance_m"] for c in stamp.decisions[0].details["competing_claims"]} == {
        40,
        200,
    }
    assert result == preview(core, [newer, old], space_radius_m=radius)


def test_equal_claims_merge_sources_despite_retrieval_time_and_numeric_spelling():
    core = record()
    old = nearby(core, "old", payload=place_payload(("park", "공원", 40)))
    newer = nearby(core, "new", payload=place_payload(("park", "공원", 40.0))).model_copy(
        update={"retrieved_at": old.retrieved_at + timedelta(hours=1)}
    )
    stamp = preview(core, [old, newer]).stamps[1]
    assert len(stamp.evidence) == 1
    assert {s.source_id for s in stamp.evidence[0].sources} == {"old", "new"}
    assert any(
        d.admission == "duplicate" and d.details["merged_into"] == stamp.evidence[0].id
        for d in stamp.decisions
    )


def test_same_entity_with_different_relation_or_scope_remains_separate():
    def item(name, role, scope, distance):
        return evidence(
            "space",
            role,
            name,
            "a" * 64,
            "same-park",
            {"distance_m": distance},
            (distance,),
            scope=scope,
        )

    a = item("point", "scene_registered_point_distance", "this-scene", 200)
    b = item("shape", "scene_geometry_distance", "this-scene", 40)
    c = item("other-scope", "scene_registered_point_distance", "other-period", 80)
    stamp = admit("scene", [a, b, c], [], SlotPolicy())
    assert len(stamp.evidence) == 3
    assert not any(d.admission in {"duplicate", "conflict"} for d in stamp.decisions)


def test_conflicting_address_codes_are_not_arbitrarily_used_as_location():
    core = record()
    a = public(core, "sgis")
    b = public(core, "sgis", address={**a.payload["address"], "emdong_cd": "620"}).model_copy(
        update={"id": "another-address"}
    )
    stamp = preview(core, [a, b]).stamps[1]
    assert stamp.location_reference is None
    assert all(d.admission == "conflict" for d in stamp.decisions)


def test_unknown_spatial_schema_does_not_inherit_distance_role():
    core = record()
    saved = nearby(core)
    piece = project_background(saved, core).pieces[0]
    with pytest.raises(ValueError, match="unsupported_spatial_relation"):
        spatial_claim(piece.model_copy(update={"schema_version": "unknown"}), saved)


def test_radius_diagnostic_reaches_html_with_actual_and_limit():
    core = record()
    result = preview(
        core, [nearby(core, payload=place_payload(("park", "공원", 190)))], space_radius_m=100
    )
    decision = next(d for d in result.stamps[1].decisions if d.reason == "outside_space_radius")
    assert decision.details["actual"] == 190
    assert decision.details["limit"] == 100
    assert decision.details["unit"] == "m"
    rendered = run_path("tools/run_diary_slots.py")["render"](result)
    assert all(s in rendered for s in ("판정값과 기준값", "outside_space_radius", "190", "100"))
