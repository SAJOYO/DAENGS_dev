"""Model-owned meaning, without filter IDs, executable actions or answer prose."""

from typing import Literal, Self

from pydantic import Field, StrictBool, model_validator

from daengs_place.place.filters.contract import KindList
from daengs_place.place.name_query import PlaceNameQuery
from daengs_place.place.planning.contract import PlanningModel

Attribute = Literal[
    "parking",
    "exclusive",
    "pet_allowed",
    "quiet",
    "free",
    "distance",
    "address",
    "selection_reason",
    "other",
]
UnsupportedAttribute = Literal["quiet", "free", "pet_allowed", "other"]


class KindEdit(PlanningModel):
    operation: Literal["set", "add", "remove"]
    values: KindList


class Alternative(PlanningModel):
    """One conjunction in a complete OR replacement. No incremental branch IDs."""

    kinds: KindList | None = None
    parking: StrictBool | None = None
    exclusive: StrictBool | None = None

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if self.kinds is None and self.parking is None and self.exclusive is None:
            raise ValueError("an alternative needs a condition")
        return self


class SemanticChanges(PlanningModel):
    kinds: KindEdit | None = None
    name_query: PlaceNameQuery | None = None
    radius_m: int | None = Field(None, ge=100, le=20000)
    parking: Literal["keep", "required_true", "required_false", "preferred_true", "clear"] = "keep"
    exclusive: Literal["keep", "required_true", "required_false", "clear"] = "keep"
    # None preserves OR scope; [] explicitly removes it. AND attributes above are global.
    alternatives: tuple[Alternative, ...] | None = Field(None, max_length=4)


class Interpretation(PlanningModel):
    goal: Literal["show", "pick_one", "explain", "edit_only", "clarify"]
    changes: SemanticChanges = Field(default_factory=SemanticChanges)
    refresh: bool = False
    reference_index: int | None = Field(None, ge=1, le=120)
    asked_attributes: tuple[Attribute, ...] = Field(default=(), max_length=9)
    unsupported: tuple[UnsupportedAttribute, ...] = Field(default=(), max_length=4)
    region_query: str = Field(default="", max_length=100)
    unresolved: Literal[
        "none", "conflicting_conditions", "missing_target", "unsupported_goal", "ambiguous"
    ] = "none"


class PendingDecision(PlanningModel):
    # A separate tool call cannot manufacture or replace a filter on acceptance.
    decision: Literal["accept", "reject", "revise", "new_request", "unclear"]
