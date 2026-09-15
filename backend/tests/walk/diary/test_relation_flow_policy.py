"""Case support, temporal selection, and the separate writer vocabulary boundary."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from daengs_walk.diary.relational.relation_delivery import flow_view
from daengs_walk.diary.relational.relation_flow_analysis import distance_flow, route_flow
from daengs_walk.diary.relational.relation_flow_contracts import DistanceSample, DistanceTrack
from daengs_walk.diary.relational.relation_injection import select_relations

START = datetime(2026, 9, 8, tzinfo=UTC)


def track(values, scope="reference_point"):
    return DistanceTrack(
        "park:a",
        "공원",
        scope,
        tuple(
            DistanceSample(START + timedelta(seconds=i * 10), v, 2, f"gps:{i}")
            for i, v in enumerate(values)
        ),
        "map-v1",
    )


def calculate(t):
    return distance_flow(t, t.samples[0].at, t.samples[-1].at)


@pytest.mark.parametrize(
    ("values", "case", "word"),
    [
        ([150, 110, 70], "distance_decrease", "drawing_closer"),
        ([40, 80, 120], "distance_increase", "leaving_behind"),
        ([150, 90, 30, 80, 110], "distance_valley", "drawing_closer_then_away"),
        ([40, 43, 41], "distance_stable", "keeping_a_similar_distance"),
    ],
)
def test_computed_cases_have_separate_delivery_words(values, case, word):
    flow = calculate(track(values))
    assert flow.case == case
    view = flow_view(flow)
    assert view["relationship"] == word
    assert (
        not {"case", "source_ids", "calculation_policy", "source_version", "central"} & view.keys()
    )


def test_passing_requires_directional_target_support_not_just_a_distance_valley():
    t = track([150, 90, 30, 80, 110], "object_boundary")
    assert calculate(t).case == "distance_valley"
    t = replace(
        t,
        samples=tuple(
            replace(s, target_axis_offset_m=offset)
            for s, offset in zip(t.samples, [-100, -50, 0, 50, 100])
        ),
    )
    assert calculate(t).case == "passing"
    assert calculate(replace(t, scope="reference_point")).case == "distance_valley"


def test_alongside_requires_spatial_extent_and_actual_movement():
    t = track([30, 32, 31], "object_boundary")
    assert calculate(t).case == "distance_stable"
    moving = replace(
        t, samples=tuple(replace(s, path_offset_m=i * 30) for i, s in enumerate(t.samples))
    )
    assert calculate(moving).case == "alongside"
    assert calculate(replace(moving, scope="reference_point")).case == "distance_stable"


def test_no_future_observation_changes_an_earlier_scene():
    t = track([150, 110, 70, 30, 80, 120])
    cut = t.samples[2].at
    earlier = distance_flow(t, START, cut)
    assert earlier == calculate(replace(t, samples=t.samples[:3]))
    assert earlier.case == "distance_decrease"


def test_a_later_valley_can_use_earlier_approach_without_repeating_an_old_valley():
    t = track([150, 110, 70, 30, 80, 120, 150])
    flow = distance_flow(t, t.samples[2].at, t.samples[4].at)
    assert flow.case == "distance_valley" and flow.started_at == START
    assert distance_flow(t, t.samples[4].at, t.samples[-1].at).case == "distance_increase"


def test_gap_chain_and_two_endpoints_do_not_establish_a_flow():
    t = track([150, 100, 50])
    assert calculate(replace(t, samples=(t.samples[0], t.samples[-1]))) is None
    assert (
        calculate(
            replace(t, samples=tuple(replace(s, continuity_id=i) for i, s in enumerate(t.samples)))
        )
        is None
    )
    gap = replace(t.samples[-1], at=START + timedelta(seconds=100))
    assert calculate(replace(t, samples=(*t.samples[:2], gap))) is None


def test_selection_replaces_only_the_comparison_of_the_same_supported_target():
    flow = calculate(track([150, 110, 70]))
    old = NS(
        id="r1",
        family="surrounding_object",
        current_evidence_ids=("b",),
        earlier_evidence_ids=("a",),
    )
    other = NS(
        id="r2",
        family="surrounding_object",
        current_evidence_ids=("d",),
        earlier_evidence_ids=("c",),
    )
    context = NS(
        current=NS(position=NS(scene_id="s2", recorded_at=flow.ended_at)),
        earlier=NS(position=NS(recorded_at=START)),
        object_identities={"a": "park:a", "b": "park:a", "c": "park:b", "d": "park:b"},
        relation_slots=NS(all_relations=lambda: (old, other)),
    )
    selected = select_relations(context, (flow,))
    assert selected.replaced_relation_ids == ("r1",)
    assert selected.spatial_relation_ids == ("r2",)
    assert selected.flows == (flow,)
    context.current.position.recorded_at = START + timedelta(seconds=10)
    assert select_relations(context, (flow,)).flows == ()


def test_return_does_not_upgrade_endpoint_proximity_without_connected_journey():
    row = {
        "id": "route:1",
        "kind": "end_near_start",
        "start_anchor": {"event_at": START.isoformat()},
        "end_anchor": {"event_at": (START + timedelta(seconds=100)).isoformat()},
    }
    assert route_flow(row, {}) is None
    assert flow_view(route_flow(row, {}, connected_return=True))["relationship"] == "coming_back"


def test_delivery_consumes_the_allowlist_without_reintroducing_old_distance_result(monkeypatch):
    from daengs_walk.diary.relational import relation_delivery
    from daengs_walk.diary.relational.relation_flow_contracts import RelationSelection

    flow = calculate(track([150, 110, 70]))
    brief = NS(context=NS(current=NS(position=NS(scene_id="s2"))), relation_ids=("old-distance",))
    selected = RelationSelection("s2", (flow,), (), ("old-distance",))
    monkeypatch.setattr(
        relation_delivery,
        "writer_view",
        lambda _: {
            "citation_ids": ["f1", "old-distance"],
            "relation_ids": ["old-distance"],
            "relation_slots": {"proximity": [{"id": "old-distance", "result": "nearer"}]},
        },
    )
    view = relation_delivery.deliver_relations(brief, selected)
    assert view["citation_ids"] == ["f1", flow.id]
    assert view["relation_ids"] == [flow.id]
    assert "relation_slots" not in view
    assert view["journey_relations"] == [flow_view(flow)]
    with pytest.raises(ValueError, match="another scene"):
        relation_delivery.deliver_relations(brief, replace(selected, scene_id="other"))


def test_turn_allocation_uses_its_event_time_and_preserves_the_wider_support():
    flow = calculate(track([150, 110, 70]))
    event_at = START + timedelta(seconds=15)
    turn = replace(
        flow,
        case="route_turn",
        target_id=None,
        target_name=None,
        target_scope=None,
        profile=(),
        ended_at=START + timedelta(seconds=40),
        interval_view={
            "representative_event_at": event_at.isoformat(),
            "event_time_precision": "estimated",
        },
    )
    context = NS(
        current=NS(position=NS(scene_id="s2", recorded_at=START + timedelta(seconds=20))),
        earlier=NS(position=NS(recorded_at=START)),
        object_identities={},
        relation_slots=NS(all_relations=lambda: ()),
    )
    selected = select_relations(context, (turn,))
    assert selected.flows == (turn,)
    assert flow_view(turn)["interval"]["ended_at"] == turn.ended_at.isoformat()
    assert flow_view(turn)["representative_event_at"] == event_at.isoformat()
