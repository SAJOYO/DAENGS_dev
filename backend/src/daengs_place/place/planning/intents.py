"""parser 제안과 실행 가능한 plan 사이의 신뢰 경계."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from daengs_place.place.planning.contract import (
    MAX_RESULTS_PER_KIND,
    CapabilityId,
    GateOrigin,
    PlaceKind,
    PlaceSearchConditions,
    PlaceSearchPlan,
    PlaceSpatialConstraint,
    PlanningModel,
)
from daengs_place.place.planning.purpose import PurposeId


class IntentSource(StrEnum):
    """서버 adapter가 붙이는 출처. extractor나 LLM tool argument로 받지 않는다."""

    STRUCTURED_REQUEST = "structured_request"
    UI_SELECTION = "ui_selection"
    USER_CONFIRMED = "user_confirmed"
    RULE_EXACT_COMMAND = "rule_exact_command"
    RULE_INFERENCE = "rule_inference"
    LLM_PROPOSAL = "llm_proposal"
    CONTEXT = "context"


class IntentRole(StrEnum):
    GOAL = "goal"
    REQUIRED_TARGET = "required_target"
    REQUIRED_CONDITION = "required_condition"
    PREFERENCE = "preference"
    ANALOGY = "analogy"
    EXCLUDED = "excluded"
    NEGATED = "negated"
    HYPOTHETICAL = "hypothetical"
    RELATIONAL = "relational"


class ActivityId(StrEnum):
    """장소 종류로 곧장 축약하면 안 되는 사용자 활동."""

    PLAY = "play"
    BUY = "buy"


class SearchObjectId(StrEnum):
    """활동과 조합하기 전에는 목적지를 확정하지 않는 대상 객체."""

    DOG_TOY = "dog_toy"


class KindIntent(PlanningModel):
    intent_type: Literal["kind"] = "kind"
    kind: PlaceKind


class PurposeIntent(PlanningModel):
    intent_type: Literal["purpose"] = "purpose"
    purpose_id: PurposeId


class BooleanCapabilityIntent(PlanningModel):
    intent_type: Literal["boolean_capability"] = "boolean_capability"
    capability_id: Literal[CapabilityId.OPERATIONS_PARKING]
    value: bool


class SemanticIntent(PlanningModel):
    """현재 capability registry에 없는 의미도 조용히 버리지 않기 위한 관찰."""

    intent_type: Literal["semantic"] = "semantic"
    concept_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_.:-]+$")


class ActivityIntent(PlanningModel):
    intent_type: Literal["activity"] = "activity"
    activity_id: ActivityId


class ObjectIntent(PlanningModel):
    intent_type: Literal["object"] = "object"
    object_id: SearchObjectId


IntentConcept = Annotated[
    KindIntent
    | PurposeIntent
    | BooleanCapabilityIntent
    | SemanticIntent
    | ActivityIntent
    | ObjectIntent,
    Field(discriminator="intent_type"),
]


class IntentProposal(PlanningModel):
    """extractor·LLM이 제안할 수 있는 내용. source/origin/locked는 의도적으로 없다."""

    role: IntentRole
    intent: IntentConcept
    evidence: str | None = Field(None, min_length=1, max_length=500)


class IntentObservation(IntentProposal):
    """서버가 신뢰 가능한 호출 경로를 확인한 뒤 source를 붙인 봉투."""

    observation_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.:-]+$")
    source: IntentSource


def observe_intent(
    proposal: IntentProposal,
    source: IntentSource,
    *,
    observation_id: str,
) -> IntentObservation:
    """검증된 adapter만 audit id와 호출 경로의 source를 부여한다."""

    return IntentObservation.model_validate(
        {
            **proposal.model_dump(mode="python"),
            "observation_id": observation_id,
            "source": source,
        }
    )


class PlannerRequest(PlanningModel):
    spatial: PlaceSpatialConstraint
    observations: tuple[IntentObservation, ...] = Field(min_length=1, max_length=20)
    limit_per_kind: int = Field(ge=1, le=MAX_RESULTS_PER_KIND)
    conditions: PlaceSearchConditions | None = None

    @model_validator(mode="after")
    def observation_ids_are_unique(self) -> Self:
        ids = [observation.observation_id for observation in self.observations]
        if len(set(ids)) != len(ids):
            raise ValueError("planner observation ids must be unique")
        return self


class AppliedIntent(PlanningModel):
    observation_ids: tuple[str, ...] = Field(min_length=1)
    capability_id: CapabilityId
    origin: GateOrigin
    locked: bool


class PlannerIssue(PlanningModel):
    observation_ids: tuple[str, ...] = ()
    code: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    blocking: bool = Field(False, exclude_if=lambda value: not value)


class PlannerStatus(StrEnum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"


class PlannerResult(PlanningModel):
    status: PlannerStatus
    plan: PlaceSearchPlan | None = None
    applied: tuple[AppliedIntent, ...] = ()
    not_applied: tuple[PlannerIssue, ...] = ()
    unsupported: tuple[PlannerIssue, ...] = ()
    clarifications: tuple[PlannerIssue, ...] = ()

    @model_validator(mode="after")
    def status_matches_payload(self) -> Self:
        if self.status is PlannerStatus.READY:
            if self.plan is None or self.clarifications:
                raise ValueError("ready planner result requires a plan and no clarification")
            if any(issue.blocking for issue in self.unsupported):
                raise ValueError("ready planner result cannot ignore a blocking intent")
            return self
        if self.plan is not None or self.applied:
            raise ValueError("non-ready planner result cannot carry an executable plan")
        if self.status is PlannerStatus.NEEDS_CLARIFICATION and not self.clarifications:
            raise ValueError("needs_clarification result requires a clarification")
        if self.status is PlannerStatus.UNSUPPORTED and not self.unsupported:
            raise ValueError("unsupported result requires an unsupported issue")
        return self
