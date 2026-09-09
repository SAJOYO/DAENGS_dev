"""Versioned, bounded filter state; unknown is never an implicit false."""

from itertools import product
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StrictInt, model_validator

from daengs_place.place.filters.capabilities import BY_ID, Capability
from daengs_place.place.name_query import PlaceNameQuery
from daengs_place.place.planning.contract import PlaceKind, PlaceSpatialConstraint, PlanningModel
from daengs_place.place.search import PlaceDogSnapshot

Identifier = Annotated[str, Field(min_length=1, max_length=100)]
KindList = Annotated[tuple[PlaceKind, ...], Field(min_length=1, max_length=6)]


class Atom(PlanningModel):
    id: Identifier
    capability: Capability
    op: Literal["eq", "in", "not_in"]
    value: StrictBool | KindList

    @model_validator(mode="after")
    def valid_operation(self) -> Self:
        if self.op not in BY_ID[self.capability].operators:
            raise ValueError("unsupported capability operator")
        if self.capability == "purpose.kind":
            if self.op not in ("in", "not_in") or isinstance(self.value, bool):
                raise ValueError("kind requires in/not_in and a nonempty kind set")
            if len(set(self.value)) != len(self.value):
                raise ValueError("kind values must be unique")
        elif self.op != "eq" or not isinstance(self.value, bool):
            raise ValueError("boolean capability requires eq and a strict boolean")
        return self


class Branch(PlanningModel):
    id: Identifier
    all: tuple[Atom, ...] = Field(min_length=1, max_length=8)


class HardFilters(PlanningModel):
    all: tuple[Atom, ...] = Field(default=(), max_length=8)
    any: tuple[Branch, ...] = Field(default=(), max_length=4)


class Preference(Atom):
    scope_kinds: KindList

    @model_validator(mode="after")
    def parking_preference_only(self) -> Self:
        if self.value not in BY_ID[self.capability].prefer_values:
            raise ValueError("only parking=true preference is supported")
        if len(set(self.scope_kinds)) != len(self.scope_kinds):
            raise ValueError("preference scope must be unique")
        return self


class ResultPolicy(PlanningModel):
    limit_per_kind: Annotated[StrictInt, Field(ge=1, le=3000)] = 20
    uncertain_limit_per_kind: Annotated[StrictInt, Field(ge=0, le=3000)] = 0


class FilterState(PlanningModel):
    contract_version: Literal["place-filter-v1"] = "place-filter-v1"
    candidate_kinds: KindList
    spatial: PlaceSpatialConstraint
    name_query: PlaceNameQuery = ""
    hard: HardFilters = Field(default_factory=HardFilters)
    preferences: tuple[Preference, ...] = Field(default=(), max_length=4)
    unknown_policy: Literal["exclude", "separate"] = "exclude"
    result_policy: ResultPolicy = Field(default_factory=ResultPolicy)
    dogs: tuple[PlaceDogSnapshot, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        kinds = set(self.candidate_kinds)
        if len(kinds) != len(self.candidate_kinds):
            raise ValueError("candidate kinds must be unique")
        if len({dog.ref for dog in self.dogs}) != len(self.dogs):
            raise ValueError("dog refs must be unique")
        atoms = [*self.hard.all, *(a for b in self.hard.any for a in b.all)]
        ids = [a.id for a in atoms] + [b.id for b in self.hard.any]
        ids += [p.id for p in self.preferences]
        if len(ids) != len(set(ids)):
            raise ValueError("filter and branch ids must be globally unique")
        if len(atoms) > 24:
            raise ValueError("filter_complexity_limit")
        for atom in atoms:
            if atom.capability == "purpose.kind" and not set(atom.value) <= kinds:
                raise ValueError("kind condition is outside candidate scope")
        for pref in self.preferences:
            if not set(pref.scope_kinds) <= kinds:
                raise ValueError("preference is outside candidate scope")
        policy = self.result_policy
        if (policy.limit_per_kind + policy.uncertain_limit_per_kind) * len(kinds) > 5000:
            raise ValueError("result budget exceeds 5000")
        if (self.unknown_policy == "separate") != (policy.uncertain_limit_per_kind > 0):
            raise ValueError("unknown policy and uncertain limit disagree")
        # At most 6 * 4 worlds: exhaustive symbolic checking is bounded and preserves OR scope.
        # Null worlds cannot satisfy a contradictory hard conjunction either.
        from daengs_place.place.filters.evaluation import evaluate_atoms

        for branch in self.hard.any or (None,):
            conjunction = (*self.hard.all, *(branch.all if branch else ()))
            worlds = [
                (kind, parking, exclusive)
                for kind, parking, exclusive in product(
                    self.candidate_kinds, (False, True), (False, True)
                )
                if evaluate_atoms(conjunction, kind, parking, exclusive) is True
            ]
            if not worlds:
                raise ValueError("contradictory_filters")
            for pref in self.preferences:
                scoped = [w for w in worlds if w[0] in pref.scope_kinds]
                if scoped and all(parking is False for _, parking, _ in scoped):
                    raise ValueError("contradictory_preference")
        return self


def guard_filter_state(state: FilterState) -> FilterState:
    """Revalidate even model_copy/model_construct and mutable nested caller values."""
    return FilterState.model_validate(state.model_dump(mode="json"))
