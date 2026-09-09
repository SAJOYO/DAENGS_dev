"""Internal deterministic entry point. HTTP, sessions and LLM edits are separate stages."""

from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.core.clock import SystemClock
from daengs_place.place.adapters import facility_place_result, medical_place_result
from daengs_place.place.contracts import PlaceResult
from daengs_place.place.filters.contract import FilterState, guard_filter_state
from daengs_place.place.filters.evaluation import evaluate, evaluate_atom, evaluate_atoms
from daengs_place.place.filters.resolver import resolve_filtered_facilities
from daengs_place.place.medical_resolver import resolve_medical_places
from daengs_place.place.planning.contract import PlaceKind, PlanningModel
from daengs_place.place.search import MEDICAL_KINDS, PerDogEvaluation, PlaceSearchHit, _hit


class AtomEvaluation(PlanningModel):
    id: str
    capability: str
    state: Literal["true", "false", "unknown"]
    expected: bool | tuple[PlaceKind, ...]
    observed: bool | str | None
    source: str
    ref: str
    as_of: str | None = None
    borrowed: bool = False
    unknown_reason: str | None = None
    parser_version: str = "place-filter-facts/1"


class FilterEvaluation(PlanningModel):
    state: Literal["true", "unknown"]
    matched_branch_ids: tuple[str, ...] = ()
    atoms: tuple[AtomEvaluation, ...] = ()


class FilterHit(PlaceSearchHit):
    filter_evaluation: FilterEvaluation


class FilterGroup(PlanningModel):
    kind: PlaceKind
    matched: tuple[FilterHit, ...] = ()
    uncertain: tuple[FilterHit, ...] = ()
    matched_truncated: bool = False
    uncertain_truncated: bool = False
    total: int | None = None


class FilterResponse(PlanningModel):
    applied_state: FilterState
    execution_status: Literal["complete"] = "complete"
    evaluated_at: datetime
    ranking_version: Literal["distance-band-500-v1"] = "distance-band-500-v1"
    groups: tuple[FilterGroup, ...]


def explain_hit(
    place: PlaceResult,
    state: FilterState,
    *,
    uncertain: bool,
    unknown_reasons: dict[str, str] | None = None,
) -> FilterHit:
    facts = place.facts
    parking = facts.parking
    exclusive = facts.pet_access.exclusive if facts.pet_access else None
    kind = place.match.kind
    verdict = evaluate(state, kind, parking, exclusive)
    if verdict is not (None if uncertain else True):
        raise RuntimeError("SQL and filter explanation disagree")
    atoms = (*state.hard.all, *(a for b in state.hard.any for a in b.all))
    explanations = []
    for atom in atoms:
        value = evaluate_atom(atom, kind, parking, exclusive)
        path = "facts.parking" if atom.capability == "operations.parking" else "facts.pet_access"
        provenance = place.field_sources.get(path) if atom.capability != "purpose.kind" else None
        source = provenance.source if provenance else place.key
        observed = (
            kind
            if atom.capability == "purpose.kind"
            else (parking if atom.capability == "operations.parking" else exclusive)
        )
        explanations.append(
            AtomEvaluation(
                id=atom.id,
                capability=atom.capability,
                state="unknown" if value is None else "true" if value else "false",
                expected=atom.value,
                observed=observed,
                source=source.source,
                ref=source.ref,
                as_of=provenance.as_of
                if provenance
                else (place.classifications[0].as_of if place.classifications else None),
                borrowed=provenance is not None,
                unknown_reason=(unknown_reasons or {}).get(atom.capability, "not_provided")
                if value is None
                else None,
            )
        )
    base = _hit(place, None)
    base.evaluations.dogs = [
        PerDogEvaluation(
            ref=dog.ref,
            dog_access=(evaluation := _hit(place, dog).evaluations).dog_access,
            restrictions=evaluation.restrictions,
        )
        for dog in state.dogs
    ]
    return FilterHit(
        **base.model_dump(),
        filter_evaluation=FilterEvaluation(
            state="unknown" if uncertain else "true",
            matched_branch_ids=tuple(
                b.id
                for b in state.hard.any
                if evaluate_atoms(b.all, kind, parking, exclusive) is True
            ),
            atoms=tuple(explanations),
        ),
    )


async def search_filtered_places(db: AsyncSession, state: FilterState) -> FilterResponse:
    """Execute validated conditions; failures propagate rather than becoming empty results."""
    state = guard_filter_state(state)
    judged_at = SystemClock().now()
    groups = []
    for kind in state.candidate_kinds:
        if kind in MEDICAL_KINDS:
            # These capabilities are uniformly unknown on authoritative medical rows.
            # Evaluate before reading/limiting; no post-limit per-row filtering occurs.
            verdict = evaluate(state, kind, None, None)
            limit = (
                state.result_policy.uncertain_limit_per_kind
                if verdict is None
                else state.result_policy.limit_per_kind
            )
            if verdict is False or (verdict is None and state.unknown_policy == "exclude"):
                groups.append(FilterGroup(kind=kind))
                continue
            rows = await resolve_medical_places(
                db,
                **state.spatial.model_dump(),
                kind=kind.value,
                limit=limit + 1,
                judge_at=judged_at,
                name_query=state.name_query,
                precise_order=True,
            )
            hits = tuple(
                explain_hit(medical_place_result(r), state, uncertain=verdict is None)
                for r in rows[:limit]
            )
            groups.append(
                FilterGroup(
                    kind=kind,
                    matched=hits if verdict is True else (),
                    uncertain=hits if verdict is None else (),
                    matched_truncated=verdict is True and len(rows) > limit,
                    uncertain_truncated=verdict is None and len(rows) > limit,
                )
            )
        else:
            matched, uncertain, cut_matched, cut_uncertain = await resolve_filtered_facilities(
                db,
                state,
                kind.value,
            )
            groups.append(
                FilterGroup(
                    kind=kind,
                    matched=tuple(
                        explain_hit(
                            facility_place_result(r),
                            state,
                            uncertain=False,
                            unknown_reasons=r.filter_unknown_reasons,
                        )
                        for r in matched
                    ),
                    uncertain=tuple(
                        explain_hit(
                            facility_place_result(r),
                            state,
                            uncertain=True,
                            unknown_reasons=r.filter_unknown_reasons,
                        )
                        for r in uncertain
                    ),
                    matched_truncated=cut_matched,
                    uncertain_truncated=cut_uncertain,
                )
            )
    return FilterResponse(applied_state=state, evaluated_at=judged_at, groups=tuple(groups))
