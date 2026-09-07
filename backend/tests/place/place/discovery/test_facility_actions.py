import pytest

from daengs_place.place.discovery.facility import (
    FacilityInternalAction,
    continue_facilities,
    start_facilities,
)
from daengs_place.place.planning.contract import CapabilityId, GateOrigin
from daengs_place.place.planning.execution import prefers_parking
from tests.place.place.discovery.test_facility import make_service, request


def action_payload(envelope, action):
    # Exercise the actual HTTP/Redis JSON round-trip, including excluded confirmation context.
    return FacilityInternalAction.model_validate(
        {
            "request": envelope.result.request.model_dump(mode="json"),
            "search_id": str(envelope.result.search_id),
            "continuation": envelope.continuation.model_dump(mode="json"),
            "action": action,
        }
    )


async def test_confirm_preserves_interpreted_parking_and_bundled_context_without_llm():
    service, calls = make_service(preferred_parking=True)
    first = await start_facilities(
        None, request(query="주차되면 좋은 카페", dogs=[{"ref": "a", "dog_weight_kg": 5}]), service
    )
    lens = first.result.lenses[0]
    result = await continue_facilities(
        None, action_payload(first, {"type": "confirm", "lens_id": lens.id}), service
    )
    assert [c[0] for c in calls] == ["llm", "db", "db"]
    plan = calls[-1][1]
    assert prefers_parking(plan)
    purpose = next(g for g in plan.gates if g.capability_id is CapabilityId.PURPOSE_KIND)
    assert purpose.origin is GateOrigin.USER_EXPLICIT and purpose.locked and not purpose.relaxable
    assert result.result.request == first.result.request
    assert result.result.search_id == first.result.search_id
    assert result.result.confirmed_lens_id == lens.id
    assert result.result.lenses[0].search.dogs == first.result.request.dogs


async def test_cost_selection_opens_search_then_confirmation_keeps_resolution():
    service, calls = make_service(cheap=True)
    first = await start_facilities(
        None, request(query="싼 카페", preferences={"parking": True}, dogs=[{"ref": "a"}]), service
    )
    assert not first.result.lenses
    signal = first.result.signals[0]
    refined = await continue_facilities(
        None,
        action_payload(
            first, {"type": "refine", "signal_id": signal.id, "option_id": "cost.travel_distance"}
        ),
        service,
    )
    assert refined.result.outcome == "results"
    assert refined.result.signals[0].selected_option_id == "cost.travel_distance"
    assert not any(n.code == "unsupported_semantic_intent" for n in refined.result.notices)
    assert "실제 가격이 아니라" in refined.result.lenses[0].note
    confirmed = await continue_facilities(
        None,
        action_payload(refined, {"type": "confirm", "lens_id": refined.result.lenses[0].id}),
        service,
    )
    assert confirmed.result.signals[0].selected_option_id == "cost.travel_distance"
    assert confirmed.result.request == first.result.request
    assert prefers_parking(calls[-1][1])
    assert [c[0] for c in calls] == ["llm", "db", "db"]


@pytest.mark.parametrize("option", ["cost.product_price", "invented"])
async def test_unavailable_or_invented_facet_does_not_search(option):
    service, calls = make_service(cheap=True)
    first = await start_facilities(None, request(query="싼 카페"), service)
    with pytest.raises(ValueError):
        await continue_facilities(
            None,
            action_payload(
                first,
                {"type": "refine", "signal_id": first.result.signals[0].id, "option_id": option},
            ),
            service,
        )
    assert [c[0] for c in calls] == ["llm"]


async def test_manual_category_conflict_cannot_be_bypassed_by_confirm():
    service, calls = make_service()
    first = await start_facilities(None, request(kinds=["hospital"]), service)
    with pytest.raises(ValueError, match="manual category"):
        await continue_facilities(
            None,
            action_payload(
                first,
                {
                    "type": "confirm",
                    "lens_id": first.continuation.planning.lenses.executable_targets[0].lens_id,
                },
            ),
            service,
        )
    assert [c[0] for c in calls] == ["llm"]
