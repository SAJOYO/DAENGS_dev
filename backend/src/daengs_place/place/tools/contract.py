"""Model arguments are separate from the state supplied by the trusted host."""

from typing import Annotated, Self

from pydantic import Field, StrictInt, model_validator

from daengs_place.place.filters.contract import (
    Atom,
    Branch,
    Identifier,
    KindList,
    Preference,
)
from daengs_place.place.name_query import PlaceNameQuery
from daengs_place.place.planning.contract import PlanningModel


class FilterChanges(PlanningModel):
    """Omitted fields are preserved; filter edits address existing stable IDs."""

    candidate_kinds: KindList | None = None
    name_query: PlaceNameQuery | None = None
    radius_m: Annotated[StrictInt, Field(ge=100, le=20000)] | None = None
    upsert_all: tuple[Atom, ...] = Field(default=(), max_length=8)
    remove_all: tuple[Identifier, ...] = Field(default=(), max_length=8)
    upsert_any: tuple[Branch, ...] = Field(default=(), max_length=4)
    remove_any: tuple[Identifier, ...] = Field(default=(), max_length=4)
    upsert_preferences: tuple[Preference, ...] = Field(default=(), max_length=4)
    remove_preferences: tuple[Identifier, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def unambiguous_edits(self) -> Self:
        for name in ("candidate_kinds", "name_query", "radius_m"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"omit {name} to preserve it; null is not an edit")
        for group in ("all", "any", "preferences"):
            upserts = [item.id for item in getattr(self, f"upsert_{group}")]
            removes = getattr(self, f"remove_{group}")
            if len(set(upserts)) != len(upserts) or len(set(removes)) != len(removes):
                raise ValueError("duplicate edit id")
            if set(upserts) & set(removes):
                raise ValueError("cannot remove and upsert the same id")
        return self
