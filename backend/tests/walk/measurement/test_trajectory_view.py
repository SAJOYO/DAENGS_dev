import pytest

from daengs_walk.trajectory_view import (
    PreparedCore,
    ReadViewManifest,
    RouteChunk,
    adopt_read_view,
    compare_measurements,
)
from tests.walk.measurement.trajectory_support import rebuild, snapshot


def manifest(measurement=None, **changes):
    return ReadViewManifest(
        **{
            "measurement": (measurement or snapshot()).ref(),
            "read_view_revision": 1,
            "event_revision": 2,
            "scene_revision": 3,
            "binding_policy_version": "binding-v1",
            "binding_state": "pending",
            "required_route_chunks": (RouteChunk(index=0, sha256="d" * 64),),
            **changes,
        }
    )


def prepared(target, **changes):
    return PreparedCore(
        **{
            "measurement": target.measurement,
            "verified_chunks": target.required_route_chunks,
            "geometry_ready": True,
            "metrics_ready": True,
            "boundaries_ready": True,
            **changes,
        }
    )


def adopt(current, target, **changes):
    return adopt_read_view(
        **{
            "scope": target.measurement.scope,
            "current": current,
            "expected_current": current,
            "target": target,
            "prepared": prepared(target),
            **changes,
        }
    )


def test_verification_is_separate_from_immutable_result_and_does_not_adopt_it():
    local, server = snapshot(), snapshot("server-S1", source="server")
    before = local.model_dump_json()
    record = compare_measurements(local, server)
    assert record.outcome == "equivalent"
    assert record.left.measurement_id != record.right.measurement_id
    assert local.model_dump_json() == before
    with pytest.raises(ValueError):
        local.measurement_id = "overwritten"


@pytest.mark.parametrize("delta,expected", [(1e-8, "equivalent"), (0.01, "mismatch")])
def test_numerical_equivalence_does_not_mean_equal_hash_or_identity(delta, expected):
    local = snapshot()
    intervals = list(local.ledger.intervals)
    intervals[1] = rebuild(intervals[1], walking_distance_m=intervals[1].walking_distance_m + delta)
    server = snapshot("server-S1", ledger=rebuild(local.ledger, intervals=intervals))
    record = compare_measurements(local, server)
    assert record.outcome == expected
    assert record.left.result_digest != record.right.result_digest
    assert record.policy.version == "trajectory-comparison-v1"


def test_prerequisite_mismatch_and_same_input_disagreement_have_different_diagnostics():
    local = snapshot()
    other_policy = snapshot("S1", key=rebuild(local.key, config_hash="f" * 64))
    assert compare_measurements(local, other_policy).outcome == "incomparable"
    assert compare_measurements(local, other_policy).reasons == ("key:config_hash",)
    intervals = list(local.ledger.intervals)
    intervals[1] = rebuild(intervals[1], reasons=("different_decision",))
    divergent = snapshot("S1", ledger=rebuild(local.ledger, intervals=intervals))
    assert compare_measurements(local, divergent).reasons == ("structure",)


def test_same_measurement_id_cannot_name_a_different_result():
    local = snapshot()
    server = snapshot(key=rebuild(local.key, engine_version="other"))
    with pytest.raises(ValueError, match="ID was reused"):
        compare_measurements(local, server)


def test_switch_to_an_equivalent_measurement_requires_the_comparison_record():
    local, server = snapshot(), snapshot("server-S1", source="server")
    current = manifest(local)
    target = manifest(server, read_view_revision=2)
    assert adopt(current, target).reason == "verification_required"
    decision = adopt(current, target, verification=compare_measurements(local, server))
    assert decision.adopted
    assert decision.active == target
    assert decision.active.binding_state == "pending"  # AI is not in core readiness.


def test_equivalent_server_binding_cannot_attach_to_local_measurement():
    local, server = snapshot(), snapshot("server-S1", source="server")
    current = manifest(local)
    target = manifest(local, read_view_revision=2, binding_state="ready", binding_revision=1)
    server_binding = manifest(server, binding_state="ready", binding_revision=1).binding_key()
    decision = adopt(
        current,
        target,
        verification=compare_measurements(local, server),
        binding_response=server_binding,
    )
    assert decision.reason == "binding_version"
    assert decision.active == current
    assert adopt(current, target, binding_response=target.binding_key()).adopted


@pytest.mark.parametrize(
    "changes",
    [
        {"verified_chunks": ()},
        {"verified_chunks": (RouteChunk(index=0, sha256="e" * 64),)},
        {"geometry_ready": False},
        {"metrics_ready": False},
        {"boundaries_ready": False},
        {"measurement": snapshot("unrelated-result").ref()},
    ],
)
def test_new_manifest_without_prepared_core_keeps_the_complete_old_view(changes):
    local, server = snapshot(), snapshot("server-S1")
    current, target = manifest(local), manifest(server, read_view_revision=2)
    decision = adopt(
        current,
        target,
        prepared=prepared(target, **changes),
        verification=compare_measurements(local, server),
    )
    assert decision.reason == "core_pending"
    assert decision.active == current


def test_late_scene_revision_cannot_overwrite_a_newer_scene_on_same_measurement():
    requested_from = manifest()
    current = manifest(
        read_view_revision=2, scene_revision=4, binding_state="ready", binding_revision=2
    )
    stale = manifest(read_view_revision=2, binding_state="ready", binding_revision=1)
    decision = adopt(
        current, stale, expected_current=requested_from, binding_response=stale.binding_key()
    )
    assert decision.reason == "stale_manifest"
    assert decision.active == current
    # Even a caller that incorrectly substitutes today's CAS cannot downgrade revisions.
    assert adopt(current, rebuild(stale, read_view_revision=3)).reason == "revision"


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_revision", 1),
        ("scene_revision", 2),
        ("binding_revision", 1),
        ("binding_policy_version", "old-policy"),
    ],
)
def test_response_must_match_the_full_expected_binding_version(field, value):
    current = manifest()
    target = manifest(read_view_revision=2, binding_state="ready", binding_revision=2)
    response = rebuild(target.binding_key(), **{field: value})
    assert adopt(current, target, binding_response=response).reason == "binding_version"


def test_old_account_response_is_rejected_even_when_new_account_has_no_active_view():
    target = manifest()
    new_scope = rebuild(target.measurement.scope, owner_id="new-account")
    decision = adopt(None, target, scope=new_scope)
    assert decision.reason == "scope"
    assert decision.active is None


def test_initial_empty_route_is_ready_without_waiting_for_a_nonexistent_chunk():
    target = manifest(required_route_chunks=())
    assert adopt(None, target).adopted


def test_forged_same_id_result_cannot_be_adopted():
    current = manifest()
    target = rebuild(
        current,
        read_view_revision=2,
        measurement=rebuild(current.measurement, result_digest="0" * 64),
    )
    assert adopt(current, target).reason == "measurement_identity"


def test_binding_revision_cannot_move_backwards_with_fresh_manifest_revision():
    current = manifest(binding_state="ready", binding_revision=5)
    target = manifest(read_view_revision=2, binding_state="ready", binding_revision=4)
    assert adopt(current, target, binding_response=target.binding_key()).reason == "revision"


def test_late_pending_state_cannot_remove_ready_binding_for_unchanged_inputs():
    current = manifest(binding_state="ready", binding_revision=5)
    target = manifest(read_view_revision=2)
    assert adopt(current, target).reason == "revision"


def test_tiny_edge_differences_must_also_satisfy_the_total_tolerance():
    local = snapshot()
    intervals = tuple(
        rebuild(i, walking_distance_m=i.walking_distance_m + 8e-8) if i.walking_distance_m else i
        for i in local.ledger.intervals
    )
    server = snapshot("S1", ledger=rebuild(local.ledger, intervals=intervals))
    record = compare_measurements(local, server)
    assert record.outcome == "mismatch"
    assert record.reasons == ("walking_total",)
