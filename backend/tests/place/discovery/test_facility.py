import json

import pytest

from daengs_place.place.discovery.facility import (
    discover_facilities,
)
from daengs_place.place.planning.execution import prefers_parking
from daengs_place.place.search import (
    PlaceDogSnapshot,
)
from tests.place.support.discovery import make_service, request


async def test_manual_context_and_dogs_reach_results_without_more_searches():
    service, calls = make_service()
    dogs = [PlaceDogSnapshot(ref="a", dog_weight_kg=5), PlaceDogSnapshot(ref="b")]
    req = request(dogs=dogs, preferences={"parking": True}, kinds=["cafe"])
    result = await discover_facilities(None, req, service)
    assert result.outcome == "results"
    assert result.request == req
    assert [c[0] for c in calls] == ["llm", "db"]
    assert calls[0][1] == "카페"  # No dog identity or fabricated prompt appended.
    assert calls[1][1].spatial == req.spatial and prefers_parking(calls[1][1])
    search = result.lenses[0].search
    assert search.dogs == dogs and search.evaluated_at is not None
    assert [e.ref for e in search.groups[0].results[0].evaluations.dogs] == ["a", "b"]
    assert result.lenses[0].applied.kinds == ["cafe"]
    encoded = result.model_dump_json()
    assert '"gates"' not in encoded and '"raw"' not in json.loads(encoded)


async def test_dog_selection_does_not_change_candidate_identity_or_order():
    service, _ = make_service()
    empty = await discover_facilities(None, request(), service)
    selected = await discover_facilities(
        None, request(dogs=[{"ref": "large", "dog_weight_kg": 50}]), service
    )
    assert (
        empty.lenses[0].search.groups[0].results[0].place
        == selected.lenses[0].search.groups[0].results[0].place
    )


async def test_conflicting_manual_category_does_not_run_db_or_silently_ignore_ui():
    service, calls = make_service()
    result = await discover_facilities(None, request(kinds=["hospital"]), service)
    assert result.outcome == "needs_clarification" and not result.lenses
    assert [c[0] for c in calls] == ["llm"]
    assert "manual.kind_conflict" in [n.code for n in result.notices]


async def test_manual_parking_does_not_relax_a_required_unsupported_condition():
    service, calls = make_service(required_parking=True)
    result = await discover_facilities(
        None, request(query="주차 필수 카페", preferences={"parking": True}), service
    )
    assert result.outcome != "results" and not result.lenses
    assert [c[0] for c in calls] == ["llm"]


async def test_parking_checkbox_does_not_promote_ai_target_to_user_confirmed():
    service, calls = make_service()
    await discover_facilities(None, request(preferences={"parking": True}), service)
    purpose = calls[1][1].gates[0]
    assert purpose.origin.value == "inferred"
    assert purpose.relaxable and not purpose.locked


async def test_final_budget_covers_dog_evaluations_and_keeps_trim_notice(monkeypatch):
    from daengs_place.place.discovery import facility

    monkeypatch.setattr(facility, "MAX_FACILITY_BYTES", 8000)
    service, _ = make_service(huge=True)
    result = await discover_facilities(
        None, request(dogs=[{"ref": str(i)} for i in range(20)]), service
    )
    assert len(result.model_dump_json().encode("utf-8")) <= 8000
    assert "response.trimmed" in [n.code for n in result.notices]
    assert result.lenses[0].search.groups[0].truncated


@pytest.mark.parametrize(
    "change",
    [
        {"query": " "},
        {"kinds": ["cafe", "cafe"]},
        {"dogs": [{"ref": "a"}, {"ref": "a"}]},
        {"dogs": [{"ref": "a", "dog_weight_kg": -1}]},
    ],
)
def test_invalid_context_is_rejected(change):
    with pytest.raises(ValueError):
        request(**change)
