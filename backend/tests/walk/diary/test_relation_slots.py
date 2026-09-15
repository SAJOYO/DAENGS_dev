"""Exercise slot routing, missing evidence, pin isolation and persisted results."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from daengs_backend.services.walk_diary.storage.relational import read_skeleton, save_skeleton
from daengs_backend.services.walk_diary.writing.relational import validate_prepared
from daengs_backend.services.walk_diary.writing.short_memory import write_with_short_memory
from daengs_walk.diary.relational.planning import make_plan
from daengs_walk.diary.relational.relations.registry import collect_relations
from daengs_walk.value_contracts import digest
from tests.walk.diary.test_relational_takeover import frame, passing_double, prepared


def test_every_family_visible_even_without_sources():
    slots = collect_relations(frame(0), None)
    assert set(slots) == {
        "background",
        "proximity",
        "continuity",
        "route_revisit",
        "movement",
        "event_context",
    }
    assert slots["proximity"]["status"] == "not_implemented"
    assert slots["continuity"]["status"] == "not_implemented"
    assert slots["event_context"]["status"] == "not_applicable"


def test_retrace_and_pace_are_routed_once_with_original_scope():
    a, b = frame(0), frame(1)
    catalog = SimpleNamespace(
        claims=[
            {"id": "original-return", "block": 0, "start_s": 10, "end_s": 50, "meaning": "retrace"},
            {
                "id": "original-slow",
                "block": 0,
                "start_s": 20,
                "end_s": 40,
                "meaning": "relative_slow",
            },
        ]
    )
    plan = make_plan(b, a, catalog)
    slots = plan["relation_slots"]
    assert slots["route_revisit"]["items"][0]["source_ids"] == ["original-return"]
    assert slots["route_revisit"]["items"][0]["duration_s"] == 40
    assert slots["movement"]["items"][0]["source_ids"] == ["original-slow"]
    assert len(plan["movement_observations"]) == 2
    b["block"] = 1
    assert (
        make_plan(b, a, catalog)["relation_slots"]["route_revisit"]["status"]
        == "insufficient_evidence"
    )


def test_event_slot_never_inherits_an_earlier_pin_or_place():
    a, b = frame(0, action=True), frame(1, None, action=True)
    item = collect_relations(b, a)["event_context"]["items"][0]
    assert item["current_space"] == []
    assert item["pin_at"] == b["anchor"]["event_at"]
    assert item["source_ids"] == ["s1:a1"]
    assert collect_relations(frame(2), b)["event_context"]["status"] == "not_applicable"


def test_unreviewed_success_suppresses_duplicate_intro_and_slots_survive_storage(tmp_path):
    result = asyncio.run(
        write_with_short_memory(prepared([frame(0), frame(1)]), send=passing_double, review=False)
    )
    cards = result["receipt"]["cards"]
    assert cards[1]["parts"]["space"]["status"] == "not_requested"
    path = tmp_path / "diary.json"
    save_skeleton(path, result)
    assert read_skeleton(path)["cards"][1]["relation_slots"] == cards[1]["relation_slots"]


def test_rehashed_slot_invention_is_rejected():
    data = deepcopy(prepared([frame(0)]))
    plan = data["snapshot"]["plans"][0]
    plan["relation_slots"]["proximity"]["status"] = "confirmed"
    plan["revision"] = digest({k: v for k, v in plan.items() if k != "revision"})
    data["revision"] = digest(data["snapshot"])
    with pytest.raises(ValueError, match="relation slots"):
        validate_prepared(data)
