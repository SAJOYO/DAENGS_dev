"""구조화된 입력을 검증 가능한 초기 PlaceSearchPlan으로 컴파일한다."""

from collections.abc import Sequence

from daengs_place.place.planning.contract import (
    CapabilityId,
    GateMode,
    GateOperator,
    GateOrigin,
    PlaceKind,
    PlaceSearchConditions,
    PlaceSearchPlan,
    PlaceSpatialConstraint,
    PlanTrace,
    PlanTraceEntry,
    SearchGate,
    UnknownPolicy,
)
from daengs_place.place.planning.guard import guard_search_plan


def build_place_search_plan(
    *,
    lat: float,
    lng: float,
    radius_m: int,
    kinds: Sequence[PlaceKind],
    limit_per_kind: int,
    conditions: PlaceSearchConditions | None = None,
    prefer_parking: bool = False,
    purpose_origin: GateOrigin = GateOrigin.USER_EXPLICIT,
    purpose_locked: bool = True,
    purpose_relaxable: bool = False,
    purpose_reason: str = "structured input selected canonical candidate kinds",
    parking_origin: GateOrigin = GateOrigin.USER_PREFERENCE,
    parking_reason: str = "structured input requested parking preference",
) -> PlaceSearchPlan:
    """LLM 없이도 UI·규칙 planner가 같은 실행 계약을 만들 수 있는 진입점."""

    gates = [
        SearchGate(
            capability_id=CapabilityId.PURPOSE_KIND,
            mode=GateMode.FILTER,
            operator=GateOperator.IN,
            value=tuple(kinds),
            unknown_policy=UnknownPolicy.EXCLUDE,
            origin=purpose_origin,
            locked=purpose_locked,
            relaxable=purpose_relaxable,
        )
    ]
    trace = [
        PlanTraceEntry(
            action="compiled",
            capability_id=CapabilityId.PURPOSE_KIND,
            origin=purpose_origin,
            reason=purpose_reason,
        )
    ]
    if prefer_parking:
        gates.append(
            SearchGate(
                capability_id=CapabilityId.OPERATIONS_PARKING,
                mode=GateMode.PREFER,
                operator=GateOperator.EQ,
                value=True,
                unknown_policy=UnknownPolicy.KEEP,
                origin=parking_origin,
                locked=False,
                relaxable=True,
            )
        )
        trace.append(
            PlanTraceEntry(
                action="compiled",
                capability_id=CapabilityId.OPERATIONS_PARKING,
                origin=parking_origin,
                reason=parking_reason,
            )
        )
    return guard_search_plan(
        PlaceSearchPlan(
            spatial=PlaceSpatialConstraint(lat=lat, lng=lng, radius_m=radius_m),
            gates=tuple(gates),
            limit_per_kind=limit_per_kind,
            conditions=conditions,
            trace=PlanTrace(entries=tuple(trace)),
        )
    )
