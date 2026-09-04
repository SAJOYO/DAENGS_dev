"""검증된 PlaceSearchPlan을 현재 resolver 입력으로 읽는 결정론적 adapter."""

from daengs_place.place.planning.contract import CapabilityId, GateMode, PlaceKind, PlaceSearchPlan


def _active_gate(plan: PlaceSearchPlan, capability_id: CapabilityId):
    return next(
        (
            gate
            for gate in plan.gates
            if gate.capability_id is capability_id and gate.mode is not GateMode.OFF
        ),
        None,
    )


def purpose_kinds(plan: PlaceSearchPlan) -> tuple[PlaceKind, ...]:
    gate = _active_gate(plan, CapabilityId.PURPOSE_KIND)
    if gate is None or not isinstance(gate.value, tuple):
        raise ValueError("validated plan must contain an active purpose.kind gate")
    return gate.value


def prefers_parking(plan: PlaceSearchPlan) -> bool:
    gate = _active_gate(plan, CapabilityId.OPERATIONS_PARKING)
    return gate is not None and gate.mode is GateMode.PREFER and gate.value is True


EXECUTORS = {
    "purpose_kind_selector": purpose_kinds,
    "parking_preference_ranker": prefers_parking,
}
