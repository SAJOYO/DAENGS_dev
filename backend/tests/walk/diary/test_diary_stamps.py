"""Selection behavior and storage-to-stamp boundaries, with synthetic evidence only."""

import uuid
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from daengs_backend.orchestration.contracts import PrincipalContext
from daengs_backend.services.walk_diary.guard import (
    StaleDiaryGeneration,
    bind_generation,
    require_current,
)
from daengs_backend.services.walk_diary.preparation import diary as service
from daengs_backend.services.walk_diary.preparation.input import InputAssembly, assemble_input
from daengs_walk.diary_input import DiaryInput, digest
from daengs_walk.diary_output import assemble_diary
from daengs_walk.diary_stamps import StampPolicy, prepare_stamps
from tests.walk.support.diary import (
    nearby,
    observation,
    place_payload,
    record,
    source,
    with_backgrounds,
)
from tests.walk.support.photo_input import (
    OWNER,
    WALK,
    context_envelope,
    entry,
    pin_context,
    row,
    walk,
)


def observed(id, minute, *, seconds=60, kind="observed_dwell"):
    raw = observation().model_dump(mode="json")
    at = observation().started_at.replace(minute=minute)
    raw.update(id=id, kind=kind, started_at=at, ended_at=at + timedelta(seconds=seconds))
    raw["anchor"].update(event_at=at, location_at=at)
    raw["anchor"]["source_fixes"] = [{"client_seq": minute, "chain_index": 0, "at": at}]
    return type(observation()).model_validate(raw)


def reasons(prepared):
    return {d.material.identity: d.reason for d in prepared.core_decisions}


def test_all_user_records_survive_target_and_keep_original_order_anchor_and_text():
    note = record("note", "15", unlocated=True)
    photo = record("photo", "05", photo=True)
    other = record("other", "10")
    snapshot = source(note, photo, other, observations=[observed("dwell", 30)])
    result = prepare_stamps(snapshot, StampPolicy(target_scene_count=2))
    bundle = assemble_diary(snapshot, result.plan, None)
    assert [s.core.identity for s in bundle.scenes] == [
        photo.ref.identity,
        other.ref.identity,
        note.ref.identity,
    ]
    assert bundle.scenes[-1].anchor.point is None
    assert bundle.scenes[-1].user_record.text == note.content.text
    assert bundle.scenes[0].anchor.location_at != bundle.scenes[0].anchor.event_at
    assert reasons(result)["observation:dwell"] == "user_target_met"
    assert result.counts["total"] == 3 and result.counts["supplemented"] == 0
    assert bundle.model_status == "not_requested"


def test_dwell_then_duration_ranking_excludes_nearby_and_overlapping_observations():
    note = record(minute="05")
    observations = [
        observed("near", 5),
        observed("long-dwell", 12, seconds=120),
        observed("overlap", 13, kind="observed_fast"),
        observed("far-fast", 28, seconds=150, kind="observed_fast"),
        observed("short-dwell", 22, seconds=30),
    ]
    result = prepare_stamps(
        source(note, observations=observations), StampPolicy(target_scene_count=3)
    )
    assert [s.core.identity for s in result.plan.scenes] == [
        note.ref.identity,
        "observation:long-dwell",
        "observation:short-dwell",
    ]
    assert reasons(result)["observation:near"] == "near_user_record_time"
    assert reasons(result)["observation:overlap"] == "overlapping_selected_observation"
    assert reasons(result)["observation:far-fast"] == "deficit_filled"


def test_no_records_or_missing_motion_can_finish_below_target_without_invented_actions():
    snapshot = source(
        observations=[observed("dwell", 12), observed("slow", 30, kind="observed_slow")]
    )
    result = prepare_stamps(snapshot, StampPolicy(target_scene_count=5))
    bundle = assemble_diary(snapshot, result.plan, None)
    assert result.counts["remaining_deficit"] == 3
    assert all(
        s.user_record is None and s.observation.action_meaning == "not_inferred"
        for s in bundle.scenes
    )
    missing = source(
        route={
            "status": "unavailable",
            "analysis_id": None,
            "input_fingerprint": None,
            "calculation_version": None,
            "reason": "not_finalized",
        },
        photos_status="not_available",
    )
    empty = prepare_stamps(missing, StampPolicy(target_scene_count=5))
    assert empty.plan.scenes == () and empty.counts["remaining_deficit"] == 5
    assert "route_unavailable" in empty.limits


def test_session_wide_note_does_not_suppress_a_local_observation():
    note = record(unlocated=True)
    note = note.model_copy(
        update={"anchor": note.anchor.model_copy(update={"time_basis": "session_fallback"})}
    )
    result = prepare_stamps(
        source(note, observations=[observed("dwell", 5)]), StampPolicy(target_scene_count=2)
    )
    assert result.counts["supplemented"] == 1


def test_deleted_record_does_not_take_a_scene_or_suppress_a_supplement():
    note = record().model_copy(update={"deleted": True, "content": None, "anchor": None})
    result = prepare_stamps(
        source(note, observations=[observed("dwell", 5)]), StampPolicy(target_scene_count=1)
    )
    assert result.plan.scenes[0].core.identity == "observation:dwell"
    assert reasons(result)[note.ref.identity] == "deleted_record"


def test_slots_keep_nearest_distinct_entities_and_do_not_copy_context_to_another_scene():
    first, second = record(), record("next", "15")
    payload = place_payload(
        ("a", "같은 이름", 20),
        ("a", "같은 이름", 25),
        ("b", "같은 이름", 30),
        ("c", "세 번째", 40),
        ("d", "네 번째", 50),
        ("bad", "범위 밖", 300),
    )
    background = nearby(first, payload=payload)
    snapshot = with_backgrounds(first, second, backgrounds=[background])
    result = prepare_stamps(snapshot, StampPolicy(target_scene_count=2, space_slots=2))
    pieces = result.plan.scenes[0].background
    assert [p.facts["source_ref"]["ref"] for p in pieces] == ["a", "b"]
    assert all(p.kind == "space_relation" for p in pieces)
    assert all(p.facts["relation"] == "distance_only_not_entry_or_visit" for p in pieces)
    assert result.plan.scenes[1].background == ()
    decisions = {d.reason for d in result.background_decisions}
    assert {
        "admit",
        "duplicate_place_reference",
        "slot_capacity",
        "invalid_provider_rows",
    } <= decisions


@pytest.mark.parametrize(
    "change,reason",
    [
        ("empty", "source_empty"),
        ("unavailable", "source_unavailable"),
        ("weather", "unsupported_projection"),
        ("unselected", "not_selected_input"),
        ("geometry", "invalid_provider_payload"),
    ],
)
def test_absence_and_unsupported_background_are_distinct_and_preserve_record(change, reason):
    core = record()
    bg = nearby(core)
    if change == "empty":
        bg = nearby(core, status="empty", payload=place_payload())
    elif change == "unavailable":
        bg = bg.model_copy(
            update={
                "status": "unavailable",
                "payload": None,
                "payload_sha256": None,
                "reason": "timeout",
            }
        )
    elif change == "weather":
        bg = bg.model_copy(update={"tags": ("environment",), "provider": "unconnected-weather"})
    elif change == "geometry":
        payload = {**bg.payload, "geometry": "park_area"}
        bg = bg.model_copy(update={"payload": payload, "payload_sha256": digest(payload)})
    snapshot = with_backgrounds(core, backgrounds=[bg])
    if change == "unselected":
        snapshot = snapshot.model_copy(update={"selected_background_ids": ()})
    result = prepare_stamps(snapshot, StampPolicy(target_scene_count=1))
    assert result.plan.scenes[0].background == ()
    assert result.background_decisions[0].reason == reason
    assert assemble_diary(snapshot, result.plan, None).scenes[0].user_record == core.content


def test_input_and_policy_order_independence_with_replay_and_unchanged_originals():
    a, b = record(), record("b", "15", photo=True)
    backgrounds = [nearby(a, "a"), nearby(b, "b")]
    snapshot = with_backgrounds(a, b, backgrounds=backgrounds, observations=[observed("dwell", 30)])
    before = deepcopy(snapshot.model_dump(mode="json"))
    policy = StampPolicy(target_scene_count=3)
    prepared = prepare_stamps(snapshot, policy)
    prepared.validate_against(snapshot)
    shuffled = deepcopy(before)
    for key in ("records", "backgrounds", "observations", "selected_background_ids"):
        shuffled[key].reverse()
    assert prepare_stamps(DiaryInput.model_validate(shuffled), policy) == prepared
    assert snapshot.model_dump(mode="json") == before
    changed_policy = policy.model_copy(update={"separation_s": 21})
    assert prepare_stamps(snapshot, changed_policy).plan.revision() != prepared.plan.revision()
    prepared.plan.scenes[0].background[0].facts["distance_m"] = 0
    with pytest.raises(ValueError, match="differs"):
        prepared.validate_against(snapshot)


def test_pending_photos_unknown_policy_and_stale_pin_fail_before_stamp_creation():
    snapshot = with_backgrounds(record(), backgrounds=[nearby(record())])
    for value in [
        snapshot.model_copy(update={"photos_status": "pending"}),
        snapshot.model_copy(update={"scene_policy_version": "unsupported"}),
        snapshot.model_copy(
            update={
                "records": (
                    record().model_copy(
                        update={"ref": record().ref.model_copy(update={"pin_revision": 2})}
                    ),
                )
            }
        ),
    ]:
        with pytest.raises(ValueError):
            prepare_stamps(value, StampPolicy(target_scene_count=1))


def test_actual_stored_entry_photo_and_envelope_reach_the_bundle_and_generation_guard():
    envelope = context_envelope()
    payload = place_payload(("a", "합성 시설", 75))
    envelope.update(status="known", payload=payload, payload_sha256=digest(payload))
    snapshot = assemble_input(walk(), None, [entry()], [], row(), [envelope]).source
    assert snapshot.selected_background_ids == (envelope["id"],)
    prepared = prepare_stamps(snapshot, StampPolicy(target_scene_count=3))
    bundle = assemble_diary(snapshot, prepared.plan, None)
    assert bundle.client_session_id == str(walk().client_session_id)
    assert len(bundle.scenes) == 2 and len(prepared.plan.scenes[0].background) == 1
    assert bundle.scenes[0].user_record.text == entry().payload["note"]
    assert bundle.scenes[1].user_record.kind == "photo"
    principal = PrincipalContext(kind="APP_USER", subject=str(OWNER))
    ticket = bind_generation(principal, snapshot, 1)
    require_current(ticket, principal, snapshot, 1)
    changed = snapshot.model_copy(
        update={"photo_manifest": snapshot.photo_manifest.model_copy(update={"revision": 2})}
    )
    with pytest.raises(StaleDiaryGeneration):
        require_current(ticket, principal, changed, 1)


async def test_internal_boundary_checks_principal_before_reading_and_keeps_transaction_with_caller(
    monkeypatch,
):
    snapshot = assemble_input(walk(), None, [entry()], [], None, []).source
    reader = AsyncMock(return_value=InputAssembly(snapshot, ()))
    monkeypatch.setattr(service, "read_input", reader)
    session = object()  # No commit/add method: preparation must not reserve or write.
    policy = StampPolicy(target_scene_count=2)
    with pytest.raises(PermissionError):
        await service.prepare_saved_diary(
            session, PrincipalContext(kind="ADMIN", subject="admin"), WALK, policy
        )
    reader.assert_not_called()
    principal = PrincipalContext(kind="APP_USER", subject=str(OWNER))
    result = await service.prepare_saved_diary(session, principal, WALK, policy)
    reader.assert_awaited_once_with(session, uuid.UUID(principal.subject), WALK)
    assert result.prepared.counts["user_records"] == 1


def test_v2_background_retains_estimate_and_uncertainty_without_relocating_the_action():
    sidecar, envelope = pin_context()
    payload = {**place_payload(("a", "합성 시설", 80)), "location_basis": "estimated"}
    envelope.update(status="known", payload=payload, payload_sha256=digest(payload))
    snapshot = assemble_input(walk(), None, [entry()], [sidecar], None, [envelope]).source
    prepared = prepare_stamps(snapshot, StampPolicy(target_scene_count=1))
    facts = prepared.plan.scenes[0].background[0].facts
    assert facts["location_basis"] == facts["position_method"] == "estimated"
    assert facts["uncertainty_m"] == 20 and facts["uncertainty_basis"] == "model_bound"
    scene = assemble_diary(snapshot, prepared.plan, None).scenes[0]
    assert scene.anchor.point.lat == 37.6  # Current pin, not original entry's 37.5.
    assert scene.user_record.text == entry().payload["note"]
