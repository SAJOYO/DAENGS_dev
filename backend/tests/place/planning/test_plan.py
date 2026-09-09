import pytest
from pydantic import ValidationError

from daengs_place.place.planning.compiler import build_place_search_plan
from daengs_place.place.planning.contract import (
    CapabilityId,
    GateMode,
    GateOperator,
    GateOrigin,
    PlaceKind,
    PlaceSearchPlan,
    PlaceSpatialConstraint,
    SearchGate,
    UnknownPolicy,
)
from daengs_place.place.planning.guard import (
    PlanValidationError,
    guard_plan_transition,
    guard_search_plan,
)
from daengs_place.place.search import PlaceSearchRequest, compile_place_search_request


def _parking_gate(
    *,
    mode: GateMode = GateMode.PREFER,
    unknown_policy: UnknownPolicy = UnknownPolicy.KEEP,
    value: bool = True,
) -> SearchGate:
    return SearchGate(
        capability_id=CapabilityId.OPERATIONS_PARKING,
        mode=mode,
        operator=GateOperator.EQ,
        value=value,
        unknown_policy=unknown_policy,
        origin=GateOrigin.USER_PREFERENCE,
    )


def test_request_compiles_spatial_boundary_separately_from_capability_gates() -> None:
    request = PlaceSearchRequest(
        lat=37.5,
        lng=127.0,
        radius_m=4200,
        kinds=["cafe", "travel"],
        limit_per_kind=30,
        preferences={"parking": True},
        conditions={"dog_weight_kg": 8.5},
    )

    plan = compile_place_search_request(request)

    assert plan.spatial == PlaceSpatialConstraint(lat=37.5, lng=127.0, radius_m=4200)
    assert [gate.capability_id for gate in plan.gates] == [
        "purpose.kind",
        "operations.parking",
    ]
    purpose, parking = plan.gates
    assert purpose.model_dump() == {
        "capability_id": "purpose.kind",
        "mode": "filter",
        "operator": "in",
        "value": ("cafe", "travel"),
        "unknown_policy": "exclude",
        "origin": "user_explicit",
        "locked": True,
        "relaxable": False,
    }
    assert parking.mode == "prefer"
    assert parking.unknown_policy == "keep"
    assert plan.conditions is not None and plan.conditions.dog_weight_kg == 8.5
    assert [entry.capability_id for entry in plan.trace.entries] == [
        "purpose.kind",
        "operations.parking",
    ]


def test_inferred_purpose_can_build_a_relaxable_candidate_pool() -> None:
    plan = build_place_search_plan(
        lat=37.5,
        lng=127.0,
        radius_m=3000,
        kinds=["travel", "leisure"],
        limit_per_kind=20,
        purpose_origin=GateOrigin.INFERRED,
        purpose_locked=False,
        purpose_relaxable=True,
    )

    assert plan.gates[0].mode == "filter"
    assert plan.gates[0].origin == "inferred"
    assert plan.gates[0].relaxable is True


@pytest.mark.parametrize(
    ("gate", "message"),
    [
        (_parking_gate(mode=GateMode.FILTER), "does not allow mode"),
        (
            _parking_gate(unknown_policy=UnknownPolicy.EXCLUDE),
            "does not allow unknown_policy",
        ),
        (_parking_gate(value=False), "does not execute value=False"),
    ],
)
def test_guard_rejects_capability_strength_or_semantics_without_an_executor(
    gate: SearchGate,
    message: str,
) -> None:
    base = compile_place_search_request(PlaceSearchRequest(lat=37.5, lng=127.0, kinds=["cafe"]))
    plan = PlaceSearchPlan(
        spatial=base.spatial,
        gates=(*base.gates, gate),
        limit_per_kind=base.limit_per_kind,
    )

    with pytest.raises(PlanValidationError, match=message):
        guard_search_plan(plan)


def test_guard_rejects_duplicate_capability_and_total_budget_overflow() -> None:
    base = compile_place_search_request(
        PlaceSearchRequest(lat=37.5, lng=127.0, kinds=["cafe", "travel"])
    )
    duplicate = PlaceSearchPlan(
        spatial=base.spatial,
        gates=(base.gates[0], base.gates[0]),
        limit_per_kind=20,
    )
    overflow = base.model_copy(update={"limit_per_kind": 3000})

    with pytest.raises(PlanValidationError, match="only one gate"):
        guard_search_plan(duplicate)
    with pytest.raises(PlanValidationError, match="must not exceed 5000"):
        guard_search_plan(overflow)


def test_system_gate_cannot_enter_an_executable_plan_unlocked() -> None:
    base = compile_place_search_request(PlaceSearchRequest(lat=37.5, lng=127.0, kinds=["cafe"]))
    unlocked_system = base.gates[0].model_copy(
        update={"origin": GateOrigin.SYSTEM, "locked": False}
    )
    plan = base.model_copy(update={"gates": (unlocked_system,)})

    with pytest.raises(PlanValidationError, match="system gates must be locked"):
        guard_search_plan(plan)


def test_locked_gate_value_cannot_change_during_plan_transition() -> None:
    previous = compile_place_search_request(PlaceSearchRequest(lat=37.5, lng=127.0, kinds=["cafe"]))
    changed = previous.gates[0].model_copy(update={"value": (PlaceKind.HOSPITAL,)})
    proposed = previous.model_copy(update={"gates": (changed,)})

    with pytest.raises(PlanValidationError, match="locked gate cannot be changed"):
        guard_plan_transition(previous, proposed)


def test_direct_plan_cannot_exceed_the_resolver_per_kind_limit() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 3000"):
        build_place_search_plan(
            lat=37.5,
            lng=127.0,
            radius_m=1000,
            kinds=["cafe"],
            limit_per_kind=5000,
        )
