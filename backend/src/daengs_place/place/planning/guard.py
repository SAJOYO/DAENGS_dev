"""AI·규칙·UI가 만든 gate를 실행 전에 같은 정책으로 검증한다."""

from pydantic import ValidationError

from daengs_place.place.planning.capabilities import capability_spec
from daengs_place.place.planning.contract import (
    MAX_KINDS_PER_REQUEST,
    MAX_TOTAL_RESULTS,
    CapabilityId,
    GateMode,
    GateOrigin,
    PlaceKind,
    PlaceSearchPlan,
)


class PlanValidationError(ValueError):
    pass


def _validate_gate_value(plan: PlaceSearchPlan, index: int) -> None:
    gate = plan.gates[index]
    spec = capability_spec(gate.capability_id)
    if spec.value_type == "boolean":
        if not isinstance(gate.value, bool):
            raise PlanValidationError(f"{gate.capability_id} requires a boolean value")
        if spec.allowed_boolean_values and gate.value not in spec.allowed_boolean_values:
            raise PlanValidationError(f"{gate.capability_id} does not execute value={gate.value}")
        return
    if not isinstance(gate.value, tuple) or not gate.value:
        raise PlanValidationError(f"{gate.capability_id} requires a non-empty kind set")
    if not all(isinstance(value, PlaceKind) for value in gate.value):
        raise PlanValidationError(f"{gate.capability_id} accepts only canonical PlaceKind values")
    if len(gate.value) > MAX_KINDS_PER_REQUEST:
        raise PlanValidationError(
            f"{gate.capability_id} accepts at most {MAX_KINDS_PER_REQUEST} kinds"
        )
    if len(set(gate.value)) != len(gate.value):
        raise PlanValidationError(f"{gate.capability_id} kinds must be unique")


def guard_search_plan(plan: PlaceSearchPlan) -> PlaceSearchPlan:
    """구조와 정책을 다시 검증한다. tool call은 이 함수를 통과해야 정책이 된다."""

    try:
        plan = PlaceSearchPlan.model_validate(plan.model_dump())
    except ValidationError as exc:
        raise PlanValidationError("invalid search plan structure") from exc

    ids = [gate.capability_id for gate in plan.gates]
    if len(set(ids)) != len(ids):
        raise PlanValidationError("a search plan may contain only one gate per capability")

    for index, gate in enumerate(plan.gates):
        spec = capability_spec(gate.capability_id)
        if gate.operator not in spec.operators:
            raise PlanValidationError(
                f"{gate.capability_id} does not allow operator={gate.operator}"
            )
        if gate.mode not in spec.modes_for(gate.origin):
            raise PlanValidationError(
                f"{gate.capability_id} does not allow mode={gate.mode} for origin={gate.origin}"
            )
        if gate.unknown_policy not in spec.unknown_policies:
            raise PlanValidationError(
                f"{gate.capability_id} does not allow unknown_policy={gate.unknown_policy}"
            )
        if gate.origin in {GateOrigin.PROFILE, GateOrigin.SYSTEM} and not gate.locked:
            raise PlanValidationError(f"{gate.origin} gates must be locked")
        if (
            gate.origin is GateOrigin.USER_EXPLICIT
            and gate.mode is GateMode.FILTER
            and not gate.locked
        ):
            raise PlanValidationError("explicit user filters must be locked")
        _validate_gate_value(plan, index)

    purpose = next(
        (
            gate
            for gate in plan.gates
            if gate.capability_id is CapabilityId.PURPOSE_KIND and gate.mode is not GateMode.OFF
        ),
        None,
    )
    if purpose is None or not isinstance(purpose.value, tuple):
        raise PlanValidationError("an executable plan requires an active purpose.kind gate")
    if plan.limit_per_kind * len(purpose.value) > MAX_TOTAL_RESULTS:
        raise PlanValidationError(
            f"limit_per_kind across all kinds must not exceed {MAX_TOTAL_RESULTS} results"
        )
    return plan


def guard_plan_transition(
    previous: PlaceSearchPlan,
    proposed: PlaceSearchPlan,
) -> PlaceSearchPlan:
    """후속 editor가 locked gate를 다시 써서 우회하지 못하게 전후 상태를 비교한다."""

    previous = guard_search_plan(previous)
    proposed = guard_search_plan(proposed)
    proposed_by_id = {gate.capability_id: gate for gate in proposed.gates}
    for locked_gate in (gate for gate in previous.gates if gate.locked):
        replacement = proposed_by_id.get(locked_gate.capability_id)
        if replacement is None:
            raise PlanValidationError(f"locked gate cannot be removed: {locked_gate.capability_id}")
        if replacement != locked_gate:
            raise PlanValidationError(f"locked gate cannot be changed: {locked_gate.capability_id}")
    return proposed
