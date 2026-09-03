"""명시적으로 확인한 search lens target만 trusted planner observation으로 승격한다."""

from collections.abc import Callable
from typing import Self
from uuid import uuid4

from pydantic import Field, model_validator

from daengs_place.place.intent.lenses import LensAvailability, TargetSearchLens
from daengs_place.place.planning.contract import CapabilityId, GateOrigin, PlanningModel
from daengs_place.place.planning.intents import (
    IntentObservation,
    IntentProposal,
    IntentRole,
    IntentSource,
    PlannerRequest,
    PlannerResult,
    PlannerStatus,
    observe_intent,
)
from daengs_place.place.planning.planner import compile_intent_plan


class ConfirmedSearchLens(PlanningModel):
    source_lens_id: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9_.:-]+$")
    confirmed_observations: tuple[IntentObservation, ...] = Field(min_length=1, max_length=6)
    modifier_ids: tuple[str, ...] = Field(max_length=10)
    unresolved_facet_ids: tuple[str, ...] = Field(max_length=10)
    result: PlannerResult

    @model_validator(mode="after")
    def confirmation_is_an_explicit_locked_plan(self) -> Self:
        if any(
            observation.source is not IntentSource.USER_CONFIRMED
            or observation.role is not IntentRole.REQUIRED_TARGET
            for observation in self.confirmed_observations
        ):
            raise ValueError("confirmed lens observations must be user-confirmed targets")
        if self.result.status is not PlannerStatus.READY or self.result.plan is None:
            raise ValueError("confirmed lens requires a ready plan")
        purpose = next(
            gate
            for gate in self.result.plan.gates
            if gate.capability_id is CapabilityId.PURPOSE_KIND
        )
        if (
            purpose.origin is not GateOrigin.USER_EXPLICIT
            or not purpose.locked
            or purpose.relaxable
        ):
            raise ValueError("confirmed lens purpose gate must be explicitly locked")
        return self


def _confirmation_suffix() -> str:
    return uuid4().hex


def confirm_search_lens(
    lens: TargetSearchLens,
    *,
    id_factory: Callable[[], str] = _confirmation_suffix,
) -> ConfirmedSearchLens:
    """서버가 발급한 실행 가능 lens의 target만 USER_CONFIRMED로 다시 계획한다."""

    original = lens.candidate.result
    if lens.availability is not LensAvailability.EXECUTABLE:
        raise ValueError("only an executable target lens can be confirmed")
    if original.status is not PlannerStatus.READY or original.plan is None:
        raise ValueError("confirmable target lens requires a ready source plan")

    confirmed = tuple(
        observe_intent(
            IntentProposal(role=IntentRole.REQUIRED_TARGET, intent=target),
            IntentSource.USER_CONFIRMED,
            observation_id=f"confirmed-target-{index}-{id_factory()}",
        )
        for index, target in enumerate(lens.confirmable_targets, start=1)
    )
    result = compile_intent_plan(
        PlannerRequest(
            spatial=original.plan.spatial,
            observations=(*confirmed, *lens.confirmation_context),
            limit_per_kind=original.plan.limit_per_kind,
            conditions=original.plan.conditions,
        )
    )
    if result.status is not PlannerStatus.READY or result.plan is None:
        raise ValueError("confirmed target could not reproduce an executable plan")

    original_purpose = next(
        gate for gate in original.plan.gates if gate.capability_id is CapabilityId.PURPOSE_KIND
    )
    confirmed_purpose = next(
        gate for gate in result.plan.gates if gate.capability_id is CapabilityId.PURPOSE_KIND
    )
    if original_purpose.value != confirmed_purpose.value:
        raise ValueError("confirmation cannot change the source lens target kinds")

    return ConfirmedSearchLens(
        source_lens_id=lens.lens_id,
        confirmed_observations=confirmed,
        modifier_ids=lens.modifier_ids,
        unresolved_facet_ids=lens.unresolved_facet_ids,
        result=result,
    )
