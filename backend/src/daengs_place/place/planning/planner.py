"""IntentObservation을 authority 정책 안에서만 실행 가능한 plan으로 승격한다."""

from daengs_place.place.planning.compiler import build_place_search_plan
from daengs_place.place.planning.contract import (
    MAX_KINDS_PER_REQUEST,
    MAX_TOTAL_RESULTS,
    CapabilityId,
    GateOrigin,
    PlaceKind,
)
from daengs_place.place.planning.intents import (
    ActivityIntent,
    AppliedIntent,
    BooleanCapabilityIntent,
    IntentObservation,
    IntentRole,
    IntentSource,
    KindIntent,
    ObjectIntent,
    PlannerIssue,
    PlannerRequest,
    PlannerResult,
    PlannerStatus,
    PurposeIntent,
    SemanticIntent,
)
from daengs_place.place.planning.purpose import resolve_purposes

_EXPLICIT_SOURCES = {
    IntentSource.STRUCTURED_REQUEST,
    IntentSource.UI_SELECTION,
    IntentSource.USER_CONFIRMED,
    IntentSource.RULE_EXACT_COMMAND,
}
_INFERRED_SOURCES = {
    IntentSource.RULE_INFERENCE,
    IntentSource.LLM_PROPOSAL,
}


def _issue(
    observation: IntentObservation,
    code: str,
    detail: str,
    *,
    blocking: bool = False,
) -> PlannerIssue:
    return PlannerIssue(
        observation_ids=(observation.observation_id,),
        code=code,
        detail=detail,
        blocking=blocking,
    )


def _target_origin(observation: IntentObservation) -> GateOrigin | None:
    if observation.source in _EXPLICIT_SOURCES:
        return GateOrigin.USER_EXPLICIT
    if observation.source in _INFERRED_SOURCES:
        return GateOrigin.INFERRED
    return None


def _preference_origin(source: IntentSource) -> GateOrigin:
    if source in _EXPLICIT_SOURCES:
        return GateOrigin.USER_PREFERENCE
    if source is IntentSource.CONTEXT:
        return GateOrigin.CONTEXT
    return GateOrigin.INFERRED


def _target_kinds(
    observations: list[IntentObservation],
) -> tuple[PlaceKind, ...]:
    selected: set[PlaceKind] = set()
    for observation in observations:
        if isinstance(observation.intent, KindIntent):
            selected.add(observation.intent.kind)
        else:
            assert isinstance(observation.intent, PurposeIntent)
            selected.update(resolve_purposes([observation.intent.purpose_id]).kinds)
    return tuple(kind for kind in PlaceKind if kind in selected)


def _without_plan(
    *,
    status: PlannerStatus,
    not_applied: list[PlannerIssue],
    unsupported: list[PlannerIssue],
    clarifications: list[PlannerIssue],
) -> PlannerResult:
    return PlannerResult(
        status=status,
        not_applied=tuple(not_applied),
        unsupported=tuple(unsupported),
        clarifications=tuple(clarifications),
    )


def compile_intent_plan(request: PlannerRequest) -> PlannerResult:
    """관찰을 검증한다. 텍스트나 confidence만으로 명시 조건을 만들지 않는다."""

    explicit_targets: list[IntentObservation] = []
    inferred_targets: list[IntentObservation] = []
    parking_preferences: list[IntentObservation] = []
    not_applied: list[PlannerIssue] = []
    unsupported: list[PlannerIssue] = []
    clarifications: list[PlannerIssue] = []
    for observation in request.observations:
        intent = observation.intent
        if isinstance(intent, (ActivityIntent, ObjectIntent)):
            unsupported.append(
                _issue(
                    observation,
                    "unresolved_compositional_intent",
                    f"{intent.intent_type} requires hypothesis normalization before planning",
                    blocking=observation.role
                    in {
                        IntentRole.GOAL,
                        IntentRole.REQUIRED_TARGET,
                        IntentRole.REQUIRED_CONDITION,
                        IntentRole.EXCLUDED,
                    },
                )
            )
            continue
        if isinstance(intent, SemanticIntent):
            unsupported.append(
                _issue(
                    observation,
                    "unsupported_semantic_intent",
                    f"no executable capability for {intent.concept_id}",
                    blocking=observation.role
                    in {
                        IntentRole.GOAL,
                        IntentRole.REQUIRED_TARGET,
                        IntentRole.REQUIRED_CONDITION,
                        IntentRole.EXCLUDED,
                    },
                )
            )
            continue

        if isinstance(intent, BooleanCapabilityIntent):
            if observation.role is IntentRole.PREFERENCE and intent.value is True:
                parking_preferences.append(observation)
            elif observation.role is IntentRole.NEGATED:
                not_applied.append(
                    _issue(observation, "negated_mention", "negated intent was not applied")
                )
            else:
                unsupported.append(
                    _issue(
                        observation,
                        "unsupported_capability_strength",
                        "parking currently supports only value=true preference",
                        blocking=observation.role
                        in {
                            IntentRole.GOAL,
                            IntentRole.REQUIRED_TARGET,
                            IntentRole.REQUIRED_CONDITION,
                            IntentRole.EXCLUDED,
                        },
                    )
                )
            continue

        assert isinstance(intent, (KindIntent, PurposeIntent))

        if observation.role is IntentRole.REQUIRED_TARGET:
            origin = _target_origin(observation)
            if origin is GateOrigin.USER_EXPLICIT:
                explicit_targets.append(observation)
            elif origin is GateOrigin.INFERRED:
                inferred_targets.append(observation)
            else:
                unsupported.append(
                    _issue(
                        observation,
                        "unsupported_target_authority",
                        "context cannot create a place-kind filter",
                        blocking=True,
                    )
                )
            continue

        if observation.role is IntentRole.NEGATED:
            not_applied.append(
                _issue(observation, "negated_mention", "negated target was not applied")
            )
        elif observation.role is IntentRole.REQUIRED_CONDITION:
            unsupported.append(
                _issue(
                    observation,
                    "invalid_kind_condition",
                    "a place kind cannot be used as a required fact condition",
                    blocking=True,
                )
            )
        elif observation.role is IntentRole.EXCLUDED:
            unsupported.append(
                _issue(
                    observation,
                    "unsupported_kind_exclusion",
                    "kind exclusion has no deterministic executor",
                    blocking=True,
                )
            )
        else:
            unsupported.append(
                _issue(
                    observation,
                    "non_literal_target",
                    f"{observation.role} cannot become a purpose.kind filter",
                )
            )

    targets = explicit_targets or inferred_targets
    if explicit_targets:
        not_applied.extend(
            _issue(
                observation,
                "shadowed_by_explicit_target",
                "an inferred target cannot widen an explicit user target",
            )
            for observation in inferred_targets
        )

    if not targets:
        if not any(issue.blocking for issue in unsupported):
            clarifications.append(
                PlannerIssue(
                    code="place_target_required",
                    detail="an executable plan requires a literal place target",
                )
            )
            status = PlannerStatus.NEEDS_CLARIFICATION
        else:
            status = PlannerStatus.UNSUPPORTED
        return _without_plan(
            status=status,
            not_applied=not_applied,
            unsupported=unsupported,
            clarifications=clarifications,
        )

    kinds = _target_kinds(targets)
    if len(kinds) > MAX_KINDS_PER_REQUEST:
        clarifications.append(
            PlannerIssue(
                observation_ids=tuple(item.observation_id for item in targets),
                code="too_many_candidate_kinds",
                detail=f"planner target exceeds the {MAX_KINDS_PER_REQUEST}-kind boundary",
            )
        )
    elif request.limit_per_kind * len(kinds) > MAX_TOTAL_RESULTS:
        clarifications.append(
            PlannerIssue(
                observation_ids=tuple(item.observation_id for item in targets),
                code="result_budget_exceeded",
                detail=f"planned candidate budget exceeds {MAX_TOTAL_RESULTS} results",
            )
        )
    if clarifications:
        return _without_plan(
            status=PlannerStatus.NEEDS_CLARIFICATION,
            not_applied=not_applied,
            unsupported=unsupported,
            clarifications=clarifications,
        )
    if any(issue.blocking for issue in unsupported):
        return _without_plan(
            status=PlannerStatus.UNSUPPORTED,
            not_applied=not_applied,
            unsupported=unsupported,
            clarifications=[],
        )

    purpose_origin = GateOrigin.USER_EXPLICIT if explicit_targets else GateOrigin.INFERRED
    purpose_ids = tuple(item.observation_id for item in targets)

    parking_origin = GateOrigin.USER_PREFERENCE
    if parking_preferences:
        parking_origin = min(
            (_preference_origin(item.source) for item in parking_preferences),
            key=(
                GateOrigin.USER_PREFERENCE,
                GateOrigin.CONTEXT,
                GateOrigin.INFERRED,
            ).index,
        )
    parking_ids = tuple(item.observation_id for item in parking_preferences)
    plan = build_place_search_plan(
        lat=request.spatial.lat,
        lng=request.spatial.lng,
        radius_m=request.spatial.radius_m,
        kinds=kinds,
        limit_per_kind=request.limit_per_kind,
        conditions=request.conditions,
        prefer_parking=bool(parking_preferences),
        purpose_origin=purpose_origin,
        purpose_locked=purpose_origin is GateOrigin.USER_EXPLICIT,
        purpose_relaxable=purpose_origin is GateOrigin.INFERRED,
        purpose_reason="planner observations: " + ", ".join(purpose_ids),
        parking_origin=parking_origin,
        parking_reason=(
            "planner observations: " + ", ".join(parking_ids)
            if parking_ids
            else "structured input requested parking preference"
        ),
    )
    applied = [
        AppliedIntent(
            observation_ids=purpose_ids,
            capability_id=CapabilityId.PURPOSE_KIND,
            origin=purpose_origin,
            locked=purpose_origin is GateOrigin.USER_EXPLICIT,
        )
    ]
    if parking_ids:
        applied.append(
            AppliedIntent(
                observation_ids=parking_ids,
                capability_id=CapabilityId.OPERATIONS_PARKING,
                origin=parking_origin,
                locked=False,
            )
        )
    return PlannerResult(
        status=PlannerStatus.READY,
        plan=plan,
        applied=tuple(applied),
        not_applied=tuple(not_applied),
        unsupported=tuple(unsupported),
    )
