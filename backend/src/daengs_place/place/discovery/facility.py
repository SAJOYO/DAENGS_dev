"""Facility-screen consumer of the existing intent/search engine; no assistant projection."""

from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.place.discovery.contract import PlaceDiscoveryPlanningData, PlaceDiscoveryRequest
from daengs_place.place.discovery.service import PlaceDiscoveryService
from daengs_place.place.intent.confirmation import confirm_search_lens
from daengs_place.place.intent.refinement import resolve_search_facet
from daengs_place.place.intent.suggestions import SuggestionResolution
from daengs_place.place.planning.compiler import build_place_search_plan
from daengs_place.place.planning.contract import (
    CapabilityId,
    GateOrigin,
    PlaceKind,
    PlaceSpatialConstraint,
    PlanningModel,
)
from daengs_place.place.planning.execution import prefers_parking, purpose_kinds
from daengs_place.place.planning.intents import IntentObservation, PlannerStatus
from daengs_place.place.presentation.contract import PlacePresentation
from daengs_place.place.search import (
    PlaceDogSnapshot,
    PlaceSearchPreferences,
    PlaceSearchResponse,
    evaluate_search_dogs,
)

MAX_FACILITY_BYTES = 256 * 1024


class FacilityDiscoveryRequest(PlanningModel):
    client_request_id: UUID
    query: str = Field(min_length=1, max_length=1000)
    spatial: PlaceSpatialConstraint
    kinds: list[PlaceKind] = Field(default_factory=list, max_length=6)
    preferences: PlaceSearchPreferences = Field(default_factory=PlaceSearchPreferences)
    dogs: list[PlaceDogSnapshot] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        if not self.query.strip():
            raise ValueError("query must not be blank")
        if len(set(self.kinds)) != len(self.kinds):
            raise ValueError("kinds must be unique")
        if len({dog.ref for dog in self.dogs}) != len(self.dogs):
            raise ValueError("dog refs must be unique")
        return self


class FacilityOption(PlanningModel):
    id: str
    label: str
    availability: str
    note: str


class FacilitySignal(PlanningModel):
    id: str
    label: str
    state: str
    required: bool
    note: str
    options: list[FacilityOption]
    selected_option_id: str | None = None


class FacilityApplied(PlanningModel):
    kinds: list[PlaceKind]
    parking: bool


class FacilityLens(PlanningModel):
    id: str
    label: str
    note: str
    applied: FacilityApplied
    search: PlaceSearchResponse
    presentations: list[PlacePresentation]


class FacilityNotice(PlanningModel):
    code: str
    message: str


class FacilityDiscoveryResponse(PlanningModel):
    contract_version: Literal["facility-discovery-v1"] = "facility-discovery-v1"
    search_id: UUID
    request: FacilityDiscoveryRequest
    outcome: Literal["results", "empty", "needs_clarification", "unsupported"]
    lenses: list[FacilityLens]
    signals: list[FacilitySignal]
    notices: list[FacilityNotice]
    confirmed_lens_id: str | None = None


class ConfirmAction(PlanningModel):
    type: Literal["confirm"]
    lens_id: str = Field(min_length=1, max_length=160)


class RefineAction(PlanningModel):
    type: Literal["refine"]
    signal_id: str = Field(min_length=1, max_length=160)
    option_id: str = Field(min_length=1, max_length=120)


class FacilityContinuation(PlanningModel):
    planning: PlaceDiscoveryPlanningData
    # TargetSearchLens deliberately excludes these from public serialization.
    # Preserve them separately only inside the backend-owned continuation.
    contexts: dict[str, tuple[IntentObservation, ...]]
    confirmed_lens_id: str | None = None


class FacilityInternalResponse(PlanningModel):
    result: FacilityDiscoveryResponse
    continuation: FacilityContinuation


class FacilityInternalAction(PlanningModel):
    request: FacilityDiscoveryRequest
    search_id: UUID
    continuation: FacilityContinuation
    action: ConfirmAction | RefineAction = Field(discriminator="type")


def continuation_for(planning, confirmed_lens_id=None):
    return FacilityContinuation(
        planning=planning,
        contexts={
            lens.lens_id: lens.confirmation_context for lens in planning.lenses.target_lenses
        },
        confirmed_lens_id=confirmed_lens_id,
    )


async def start_facilities(db, request, service) -> FacilityInternalResponse:
    planning = await service.plan(
        PlaceDiscoveryRequest(query=request.query, spatial=request.spatial)
    )
    result = await execute_facilities(db, request, service, planning)
    return FacilityInternalResponse(result=result, continuation=continuation_for(planning))


async def continue_facilities(
    db, payload: FacilityInternalAction, service
) -> FacilityInternalResponse:
    saved = payload.continuation
    planning = saved.planning
    lenses = planning.lenses.model_copy(
        update={
            "target_lenses": tuple(
                lens.model_copy(
                    update={"confirmation_context": saved.contexts.get(lens.lens_id, ())}
                )
                for lens in planning.lenses.target_lenses
            )
        }
    )
    confirmed_id = saved.confirmed_lens_id
    if isinstance(payload.action, RefineAction):
        lenses = resolve_search_facet(
            lenses, signal_lens_id=payload.action.signal_id, option_id=payload.action.option_id
        )
    else:
        lens = next(
            (item for item in lenses.executable_targets if item.lens_id == payload.action.lens_id),
            None,
        )
        if lens is None:
            raise ValueError("unknown or unavailable target")
        confirmed = confirm_search_lens(lens)
        if payload.request.kinds and not set(purpose_kinds(confirmed.result.plan)).intersection(
            payload.request.kinds
        ):
            raise ValueError("target conflicts with manual category")
        lens = lens.model_copy(
            update={"candidate": lens.candidate.model_copy(update={"result": confirmed.result})}
        )
        lenses = lenses.model_copy(update={"target_lenses": (lens,)})
        confirmed_id = lens.lens_id
    ready = bool(lenses.executable_targets)
    planning = planning.model_copy(
        update={
            "lenses": lenses,
            "status": PlannerStatus.READY if ready else planning.status,
            "resolution": (planning.resolution or SuggestionResolution.INFERRED)
            if ready
            else planning.resolution,
        }
    )
    result = await execute_facilities(
        db,
        payload.request,
        service,
        planning,
        search_id=payload.search_id,
        confirmed_lens_id=confirmed_id,
    )
    return FacilityInternalResponse(
        result=result, continuation=continuation_for(planning, confirmed_id)
    )


async def discover_facilities(
    db: AsyncSession, request: FacilityDiscoveryRequest, service: PlaceDiscoveryService
) -> FacilityDiscoveryResponse:
    """Intersect manual kinds, add parking preference, then evaluate dogs after search.

    Only existing executable plans are adjusted. Unsupported mandatory conditions and
    unresolved facets remain blocked; manual settings never rescue an unsafe interpretation.
    """
    return (await start_facilities(db, request, service)).result


async def execute_facilities(
    db, request, service, planning, *, search_id=None, confirmed_lens_id=None
):
    notices = [
        FacilityNotice(
            code="spatial.explicit",
            message="선택한 지도 위치와 반경으로 검색했어요. 문장 속 지역명으로 위치를 옮기지는 않아요.",
        )
    ]
    targets = []
    applied = {}
    for lens in planning.lenses.executable_targets:
        original = lens.candidate.result.plan
        assert original is not None
        kinds = list(purpose_kinds(original))
        if request.kinds:
            kinds = [kind for kind in kinds if kind in request.kinds]
            if not kinds:
                notices.append(
                    FacilityNotice(
                        code="manual.kind_conflict",
                        message=f"{lens.display_label}: 선택한 카테고리와 겹치지 않아 검색하지 않았어요.",
                    )
                )
                continue
        parking = request.preferences.parking or prefers_parking(original)
        purpose = next(g for g in original.gates if g.capability_id is CapabilityId.PURPOSE_KIND)
        parking_gate = next(
            (g for g in original.gates if g.capability_id is CapabilityId.OPERATIONS_PARKING), None
        )
        # Recompile through the existing compiler/guard; never accept client-supplied gates.
        plan = (
            build_place_search_plan(
                lat=request.spatial.lat,
                lng=request.spatial.lng,
                radius_m=request.spatial.radius_m,
                kinds=kinds,
                limit_per_kind=original.limit_per_kind,
                prefer_parking=parking,
                purpose_origin=GateOrigin.USER_EXPLICIT if request.kinds else purpose.origin,
                purpose_locked=True if request.kinds else purpose.locked,
                purpose_relaxable=False if request.kinds else purpose.relaxable,
                purpose_reason="manual category intersection"
                if request.kinds
                else "preserved interpreted target",
                parking_origin=GateOrigin.USER_PREFERENCE
                if request.preferences.parking
                else (parking_gate.origin if parking_gate else GateOrigin.USER_PREFERENCE),
            )
            if request.kinds or request.preferences.parking
            else original
        )
        target = lens.model_copy(
            update={
                "candidate": lens.candidate.model_copy(
                    update={
                        "result": lens.candidate.result.model_copy(update={"plan": plan}),
                    }
                )
            }
        )
        targets.append(target)
        applied[lens.lens_id] = FacilityApplied(kinds=kinds, parking=parking)

    execution_planning = planning.model_copy(
        update={
            "lenses": planning.lenses.model_copy(
                update={"target_lenses": tuple(targets)},
            )
        }
    )
    if not targets and planning.status is PlannerStatus.READY:
        execution_planning = execution_planning.model_copy(
            update={
                "status": PlannerStatus.NEEDS_CLARIFICATION,
                "resolution": None,
            }
        )
    discovery = await service.execute(
        db,
        execution_planning,
        PlaceDiscoveryRequest(query=request.query, spatial=request.spatial).result_policy,
    )
    lenses = [
        FacilityLens(
            id=item.lens_id,
            label=item.display_label,
            note=item.support_note,
            applied=applied[item.lens_id],
            search=evaluate_search_dogs(item.search, request.dogs),
            presentations=list(item.presentations),
        )
        for item in discovery.lens_results
    ]
    signals = [
        FacilitySignal(
            id=item.lens_id,
            label=item.display_label,
            state=item.availability.value,
            required=item.required,
            note=item.support_note,
            options=[
                FacilityOption(
                    id=option.option_id,
                    label=option.display_label,
                    availability=option.availability.value,
                    note=option.support_note,
                )
                for option in item.options
            ],
            selected_option_id=item.selected_option_id,
        )
        for item in planning.lenses.signal_lenses
    ]
    notices.extend(
        FacilityNotice(code=item.code, message=item.message) for item in discovery.notices
    )
    resolved_ids = {
        observation_id
        for signal in planning.lenses.signal_lenses
        if signal.selected_option_id is not None
        for observation_id in signal.basis_observation_ids
    }
    notices.extend(
        FacilityNotice(
            code=issue.code,
            message="요청의 일부 조건을 검색에 적용할 수 없어요. 검색 문장을 구체적으로 바꿔 주세요.",
        )
        for issue in planning.issues
        if not (
            issue.code == "unsupported_semantic_intent"
            and issue.observation_ids
            and set(issue.observation_ids).issubset(resolved_ids)
        )
    )
    if not targets and planning.status.value == "ready":
        notices.append(
            FacilityNotice(
                code="selection.required", message="검색 조건이나 카테고리를 확인해 주세요."
            )
        )
    if lenses:
        outcome = (
            "results" if any(g.results for lens in lenses for g in lens.search.groups) else "empty"
        )
    else:
        selectable = any(
            lens.availability.value == "needs_selection" for lens in planning.lenses.target_lenses
        )
        outcome = (
            "unsupported"
            if planning.status.value == "unsupported" and not selectable
            else "needs_clarification"
        )
    result = FacilityDiscoveryResponse(
        search_id=search_id or uuid4(),
        request=request,
        outcome=outcome,
        lenses=lenses,
        signals=signals,
        notices=notices,
        confirmed_lens_id=confirmed_lens_id,
    )
    # Per-dog annotations are added after the discovery budget, so enforce a final byte cap too.
    trimmed = False
    while len(result.model_dump_json().encode("utf-8")) > MAX_FACILITY_BYTES - 1024:
        candidates = [
            (lens, group) for lens in result.lenses for group in lens.search.groups if group.results
        ]
        if not candidates:
            raise ValueError("facility response metadata exceeds byte budget")
        lens, group = candidates[-1]
        hit = group.results.pop()
        group.truncated = True
        lens.presentations[:] = [p for p in lens.presentations if p.place_key != hit.place.key]
        if not trimmed:
            result.notices.append(
                FacilityNotice(
                    code="response.trimmed", message="응답 크기 제한으로 일부 후보를 생략했어요."
                )
            )
            trimmed = True
    if outcome == "results" and not any(
        g.results for lens in result.lenses for g in lens.search.groups
    ):
        result = result.model_copy(update={"outcome": "empty"})
    return result
