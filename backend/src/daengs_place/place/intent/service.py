"""LLM proposal → evidence grounding → suggestion policy의 orchestration 진입점."""

from collections.abc import Callable

from daengs_place.place.intent.contract import (
    IntentEvidenceError,
    IntentProposer,
    IntentProposerInvalidOutputError,
    LLMIntentOutput,
    MaterializedIntentOutput,
    materialize_llm_output,
)
from daengs_place.place.intent.hypotheses import (
    NormalizedIntentOutput,
    build_search_hypotheses,
)
from daengs_place.place.intent.lenses import SearchLensOutcome, compile_search_lenses
from daengs_place.place.intent.suggestions import (
    IntentSuggestionOutcome,
    compile_intent_suggestions,
)
from daengs_place.place.planning.contract import (
    PlaceSearchConditions,
    PlaceSpatialConstraint,
    PlanningModel,
)
from daengs_place.place.planning.intents import PlannerIssue, PlannerStatus


class PlaceIntentSuggestionTrace(PlanningModel):
    """검증 화면이 모델 출력·grounding·정책 결과를 한 요청에서 대조하는 내부 trace."""

    raw: LLMIntentOutput | None
    grounded: MaterializedIntentOutput | None
    normalized: NormalizedIntentOutput | None
    lenses: SearchLensOutcome | None
    outcome: IntentSuggestionOutcome


class PlaceIntentSuggestionService:
    def __init__(
        self,
        proposer: IntentProposer,
        *,
        observation_id_factory: Callable[[], str] | None = None,
    ):
        self._proposer = proposer
        self._observation_id_factory = observation_id_factory

    async def suggest(
        self,
        utterance: str,
        *,
        spatial: PlaceSpatialConstraint,
        limit_per_kind: int,
        conditions: PlaceSearchConditions | None = None,
    ) -> IntentSuggestionOutcome:
        return (
            await self.inspect(
                utterance,
                spatial=spatial,
                limit_per_kind=limit_per_kind,
                conditions=conditions,
            )
        ).outcome

    async def inspect(
        self,
        utterance: str,
        *,
        spatial: PlaceSpatialConstraint,
        limit_per_kind: int,
        conditions: PlaceSearchConditions | None = None,
    ) -> PlaceIntentSuggestionTrace:
        try:
            raw = await self._proposer.propose(utterance)
        except IntentProposerInvalidOutputError:
            return PlaceIntentSuggestionTrace(
                raw=None,
                grounded=None,
                normalized=None,
                lenses=None,
                outcome=IntentSuggestionOutcome(
                    status=PlannerStatus.NEEDS_CLARIFICATION,
                    source_disposition=None,
                    issues=(
                        PlannerIssue(
                            code="intent_proposer_invalid_output",
                            detail="intent proposer output did not satisfy the intent contract",
                        ),
                    ),
                ),
            )
        try:
            if self._observation_id_factory is None:
                grounded = materialize_llm_output(utterance, raw)
            else:
                grounded = materialize_llm_output(
                    utterance,
                    raw,
                    id_factory=self._observation_id_factory,
                )
        except IntentEvidenceError:
            return PlaceIntentSuggestionTrace(
                raw=raw,
                grounded=None,
                normalized=None,
                lenses=None,
                outcome=IntentSuggestionOutcome(
                    status=PlannerStatus.NEEDS_CLARIFICATION,
                    source_disposition=raw.disposition,
                    issues=(
                        PlannerIssue(
                            code="intent_evidence_invalid",
                            detail="intent proposal evidence could not be grounded in the utterance",
                        ),
                    ),
                ),
            )
        normalized = build_search_hypotheses(grounded)
        outcome = compile_intent_suggestions(
            grounded,
            spatial=spatial,
            limit_per_kind=limit_per_kind,
            conditions=conditions,
        )
        return PlaceIntentSuggestionTrace(
            raw=raw,
            grounded=grounded,
            normalized=normalized,
            lenses=compile_search_lenses(
                normalized,
                outcome,
                spatial=spatial,
                limit_per_kind=limit_per_kind,
                conditions=conditions,
            ),
            outcome=outcome,
        )
