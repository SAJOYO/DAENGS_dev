"""Synthetic contract boundaries; no device data, provider calls or database."""

from copy import deepcopy

import pytest

from daengs_backend.orchestration.contracts import PrincipalContext
from daengs_backend.services.walk_diary.guard import (
    StaleDiaryGeneration,
    bind_generation,
    require_current,
)
from daengs_walk.diary_input import (
    Anchor,
    DiaryInput,
    MovementObservation,
    SavedBackground,
    digest,
    material_ref,
)
from daengs_walk.diary_output import (
    BackgroundPiece,
    DiaryPlan,
    DiaryWriting,
    SceneStamp,
    WritingReceipt,
    assemble_diary,
)
from tests.walk.support.diary import observation, record, source


def background(core, id="context", tags=("space",)):
    payload = {"nearby": [{"name": "공원", "distance_m": 80}]}
    return SavedBackground(
        id=id,
        target=material_ref(core),
        provider="fixture",
        policy_version="context-v1",
        payload_schema="nearby-fixture-v1",
        query_point=core.anchor.point,
        tags=tags,
        status="known",
        retrieved_at="2026-09-09T01:00:00Z",
        temporal_basis="lookup_snapshot",
        payload=payload,
        payload_sha256=digest(payload),
    )


def plan(snapshot, *cores, target=5, pieces=None):
    return DiaryPlan(
        input_revision=snapshot.revision(),
        target_scene_count=target,
        scenes=tuple(
            SceneStamp(
                id=f"scene-{i}", core=material_ref(core), background=(pieces or {}).get(i, ())
            )
            for i, core in enumerate(cores)
        ),
    )


def prepared():
    first, second = record(), record("photo", "15", photo=True)
    contexts = [background(first), background(second, "other")]
    snapshot = source(
        first, second, backgrounds=contexts, selected_background_ids=["context", "other"]
    )
    stamps = plan(
        snapshot,
        first,
        second,
        pieces={
            i: (
                BackgroundPiece(
                    id=f"bg-{i}",
                    background_id=c.id,
                    kind="space_relation",
                    schema_version="relation-v1",
                    facts={"relation": "near", "place_type": "park", "distance_m": 80},
                ),
            )
            for i, c in enumerate(contexts)
        },
    )
    writing = DiaryWriting.model_validate(
        {
            "title": "공원 가까이에서 남긴 기록",
            "scenes": [
                {
                    "scene_id": "scene-0",
                    "text": "공원이 가까이에 있었다.",
                    "evidence_ids": ["bg-0"],
                },
                {"scene_id": "scene-1", "text": None, "evidence_ids": []},
            ],
        }
    )
    return snapshot, stamps, WritingReceipt(plan_revision=stamps.revision(), writing=writing)


def test_roundtrip_preserves_user_text_event_and_sample_time_without_llm_fields():
    snapshot, stamps, receipt = prepared()
    result = assemble_diary(snapshot, stamps, receipt)
    scene = result.scenes[0]
    assert scene.user_record.text == "  두부와 사진을 찍었다.\n다음 기록도 남김  "
    assert scene.anchor == snapshot.records[0].anchor
    assert scene.anchor.event_at != scene.anchor.location_at
    assert scene.narration.text == "공원이 가까이에 있었다."
    assert result.scenes[1].narration.status == "omitted"
    assert [s.order for s in result.scenes] == [1, 2]
    assert result.title_origin == "model" and result.semantic_status == "not_evaluated"


def test_input_revision_is_stable_for_collection_order_and_equivalent_timezone():
    snapshot, _, _ = prepared()
    raw = snapshot.model_dump(mode="json")
    raw["records"].reverse()
    raw["backgrounds"].reverse()
    raw["selected_background_ids"].reverse()
    raw["started_at"] = "2026-09-09T09:00:00+09:00"
    assert DiaryInput.model_validate(raw).revision() == snapshot.revision()


@pytest.mark.parametrize(
    "change", ["note", "pin", "delete", "policy", "route", "photo_sync", "context_selection"]
)
def test_semantic_input_changes_invalidate_prepared_plan(change):
    original = record()
    snapshot = source(original)
    stamps = plan(snapshot, original)
    raw = snapshot.model_dump(mode="json")
    if change == "note":
        raw["records"][0]["content"]["text"] += " 수정"
    elif change == "pin":
        raw["records"][0]["ref"]["pin_revision"] += 1
    elif change == "delete":
        raw["records"][0].update(deleted=True, content=None, anchor=None)
    elif change == "policy":
        raw["writing_policy_version"] = "new-writing"
    elif change == "route":
        raw["route"]["input_fingerprint"] = "c" * 64
    elif change == "photo_sync":
        raw["photos_status"] = "not_available"
    else:
        raw["backgrounds"] = [background(original).model_dump(mode="json")]
        raw["selected_background_ids"] = ["context"]
    changed = DiaryInput.model_validate(raw)
    assert snapshot.revision() != changed.revision()
    with pytest.raises(ValueError, match="input revision"):
        assemble_diary(changed, stamps, None)


def test_context_from_before_pin_revision_change_cannot_be_reused():
    snapshot, _, _ = prepared()
    raw = snapshot.model_dump(mode="json")
    raw["records"][0]["ref"]["pin_revision"] += 1
    with pytest.raises(ValueError, match="stale"):
        DiaryInput.model_validate(raw)


def test_context_query_center_must_match_the_target_pin():
    snapshot, _, _ = prepared()
    raw = snapshot.model_dump(mode="json")
    raw["backgrounds"][0]["query_point"]["lat"] += 0.01
    with pytest.raises(ValueError, match="confirmed target"):
        DiaryInput.model_validate(raw)


def test_nested_provider_mutation_is_detected_at_publication_boundary():
    snapshot, stamps, receipt = prepared()
    snapshot.backgrounds[0].payload["nearby"][0]["distance_m"] = 1
    with pytest.raises(ValueError, match="hash"):
        assemble_diary(snapshot, stamps, receipt)


def test_position_comparison_is_invalidated_when_its_previous_pin_changes():
    previous, current = record(), record("current", "15")
    comparison = background(current).model_copy(
        update={"supporting_targets": (material_ref(previous),)}
    )
    snapshot = source(previous, current, backgrounds=[comparison])
    raw = snapshot.model_dump(mode="json")
    raw["records"][0]["ref"]["pin_revision"] += 1
    with pytest.raises(ValueError, match="stale supporting"):
        DiaryInput.model_validate(raw)


def test_changed_dictionary_cannot_accept_receipt_from_previous_preparation():
    snapshot, stamps, receipt = prepared()
    stamps.scenes[0].background[0].facts["distance_m"] = 90
    with pytest.raises(ValueError, match="prepared plan"):
        assemble_diary(snapshot, stamps, receipt)


@pytest.mark.parametrize(
    "field,value",
    [("action", "주인이 뛰었다"), ("anchor", {}), ("title", "장면 제목"), ("how", "직진")],
)
def test_writer_cannot_supply_original_action_anchor_how_or_scene_title(field, value):
    _, _, receipt = prepared()
    raw = receipt.writing.model_dump(mode="json")
    raw["scenes"][0][field] = value
    with pytest.raises(ValueError, match="Extra inputs"):
        DiaryWriting.model_validate(raw)


@pytest.mark.parametrize("change", ["cross_scene", "duplicate", "missing", "unknown"])
def test_writer_scene_and_citation_scope(change):
    snapshot, stamps, receipt = prepared()
    raw = receipt.writing.model_dump(mode="json")
    if change == "cross_scene":
        raw["scenes"][0]["evidence_ids"] = ["bg-1"]
    elif change == "duplicate":
        raw["scenes"][1] = deepcopy(raw["scenes"][0])
    elif change == "missing":
        raw["scenes"].pop()
    else:
        raw["scenes"][0]["scene_id"] = "another-scene"
    response = WritingReceipt(
        plan_revision=stamps.revision(), writing=DiaryWriting.model_validate(raw)
    )
    with pytest.raises(ValueError):
        assemble_diary(snapshot, stamps, response)


def test_unlocated_user_record_survives_and_does_not_invent_a_pin():
    original = record(unlocated=True)
    snapshot = source(original, photos_status="not_available")
    result = assemble_diary(snapshot, plan(snapshot, original), None)
    assert result.scenes[0].user_record == original.content
    assert result.scenes[0].anchor.point is None
    assert result.model_status == "not_requested"
    assert result.photos_status == "not_available"
    assert result.scenes[0].narration.status == "no_background"


@pytest.mark.parametrize("tags", [("space",), ("space", "time"), ("environment", "time")])
def test_adding_time_tag_does_not_allow_spatial_lookup_without_location(tags):
    original = record(unlocated=True)
    with pytest.raises(ValueError, match="confirmed target"):
        source(original, backgrounds=[background(original, tags=tags)])


def test_start_weather_cannot_be_claimed_as_later_scene_weather():
    original = record(minute="35")
    raw = background(original, tags=("environment",)).model_dump(mode="json")
    raw.update(
        temporal_basis="event_observation",
        valid_from="2026-09-09T00:00:00Z",
        valid_until="2026-09-09T00:10:00Z",
    )
    with pytest.raises(ValueError, match="does not cover"):
        source(original, backgrounds=[raw])


def test_observation_supplements_deficit_but_never_displaces_user_record():
    original, observed = record(), observation()
    snapshot = source(original, observations=[observed])
    result = assemble_diary(snapshot, plan(snapshot, original, observed, target=5), None)
    assert len(result.scenes) == 2  # Do not invent three more records to hit the target.
    assert result.scenes[1].observation.subject == "recording_device"
    with pytest.raises(ValueError, match="displaced"):
        assemble_diary(snapshot, plan(snapshot, observed), None)
    with pytest.raises(ValueError, match="deficit"):
        assemble_diary(snapshot, plan(snapshot, original, observed, target=1), None)


def test_all_user_records_survive_soft_target_and_deleted_records_do_not():
    a, b = record(), record("second", "15")
    deleted = record("gone").model_copy(update={"deleted": True, "content": None, "anchor": None})
    snapshot = source(a, deleted, b)
    result = assemble_diary(snapshot, plan(snapshot, a, b, target=1), None)
    assert [s.core.identity for s in result.scenes] == ["walk_entry:note", "walk_entry:second"]
    with pytest.raises(ValueError):
        assemble_diary(snapshot, plan(snapshot, a, deleted, b), None)


def test_scene_number_follows_event_time_even_if_photo_storage_order_differs():
    a, b = record(), record("photo", "15", photo=True)
    snapshot = source(b, a)
    with pytest.raises(ValueError, match="event time"):
        assemble_diary(snapshot, plan(snapshot, b, a), None)


def test_absolute_address_stays_separate_and_needs_no_model_call():
    snapshot, stamps, _ = prepared()
    raw = stamps.model_dump(mode="json")
    for s in raw["scenes"]:
        s["background"][0].update(
            kind="place_reference", schema_version="dong-v1", facts={"dong": "합정동"}
        )
    result = assemble_diary(snapshot, DiaryPlan.model_validate(raw), None)
    assert result.scenes[0].place_reference[0].facts["dong"] == "합정동"
    assert result.scenes[0].narration.status == "no_background"
    assert result.title_origin == "system"


def test_model_failure_keeps_original_records_and_is_not_a_refusal():
    snapshot, stamps, _ = prepared()
    result = assemble_diary(snapshot, stamps, None, failure_code="provider_failed")
    assert result.failure_code == "provider_failed" and result.model_status == "unavailable"
    assert [s.user_record for s in result.scenes] == [r.content for r in snapshot.records]
    assert all(s.narration.status == "unavailable" for s in result.scenes)


def test_observation_cannot_cross_route_analysis_or_use_estimated_anchor():
    observed = observation()
    with pytest.raises(ValueError, match="finalized route"):
        source(observations=[observed.model_copy(update={"analysis_id": "another"})])
    raw = observed.model_dump(mode="json")
    raw["anchor"]["method"] = "estimated"
    with pytest.raises(ValueError, match="observed route point"):
        MovementObservation.model_validate(raw)


def test_estimated_pin_cannot_bridge_pause_chains():
    raw = record().anchor.model_dump(mode="json")
    raw.update(
        method="estimated",
        source_fixes=[
            {"client_seq": 1, "chain_index": 0, "at": "2026-09-09T00:04:00Z"},
            {"client_seq": 2, "chain_index": 1, "at": "2026-09-09T00:06:00Z"},
        ],
    )
    with pytest.raises(ValueError, match="pause chain"):
        Anchor.model_validate(raw)


def test_observed_pin_cannot_copy_a_future_sample():
    raw = record().anchor.model_dump(mode="json")
    raw["location_at"] = "2026-09-09T00:06:00Z"
    with pytest.raises(ValueError, match="future"):
        Anchor.model_validate(raw)


def test_generation_reuses_verified_principal_and_rejects_stale_completions():
    snapshot = source(record())
    principal = PrincipalContext(subject="owner", kind="APP_USER")
    ticket = bind_generation(principal, snapshot, 2)
    require_current(ticket, principal, snapshot, 2)
    for stale in [
        snapshot.model_copy(update={"client_session_id": "different"}),
        snapshot.model_copy(update={"writing_policy_version": "next"}),
    ]:
        with pytest.raises(StaleDiaryGeneration):
            require_current(ticket, principal, stale, 2)
    with pytest.raises(StaleDiaryGeneration):
        require_current(ticket, principal, snapshot, 3)


@pytest.mark.parametrize(
    "principal",
    [
        PrincipalContext(subject="other", kind="APP_USER"),
        PrincipalContext(subject="owner", kind="ADMIN"),
    ],
)
def test_walk_conditions_access_does_not_grant_diary_ownership(principal):
    with pytest.raises(PermissionError):
        bind_generation(principal, source(record()), 1)


def test_pending_photo_sync_is_not_treated_as_an_empty_photo_collection():
    original = record()
    snapshot = source(original, photos_status="pending")
    with pytest.raises(ValueError, match="photo snapshot"):
        bind_generation(PrincipalContext(subject="owner", kind="APP_USER"), snapshot, 1)
    with pytest.raises(ValueError, match="photo snapshot"):
        assemble_diary(snapshot, plan(snapshot, original), None)
