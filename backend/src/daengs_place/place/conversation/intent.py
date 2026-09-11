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
    search_scope: Literal["keep", "bookmarks", "all_places"] = Field(
        default="keep",
        description="후보 집합 변경만 표현한다. 검색 동사나 찜 저장 행위의 부정은 집합 변경이 아니다.",
    )
    spatial_scope: Literal["keep", "unbounded"] = "keep"
    feedback: Literal["none", "evaluation", "familiarity", "information_dispute"] = "none"
    bookmark: "BookmarkEdit | None" = None
    changes: SemanticChanges = Field(default_factory=SemanticChanges)
    refresh: bool = False
    browse: Literal["current", "next", "restart"] = "current"
    place_edit: "PlaceEdit | None" = None
    reference_index: int | None = Field(None, ge=1, le=120)
    asked_attributes: tuple[Attribute, ...] = Field(default=(), max_length=9)
    unsupported: tuple[UnsupportedAttribute, ...] = Field(default=(), max_length=4)
    region_query: str = Field(default="", max_length=100)
    unresolved: Literal[
        "none", "conflicting_conditions", "missing_target", "unsupported_goal", "ambiguous"
    ] = "none"

    @model_validator(mode="after")
    def exploration_goal(self) -> Self:
        if (self.browse != "current" or self.place_edit) and self.goal != "show":
            raise ValueError("exploration edits require show")
        if self.browse == "restart" and self.place_edit:
            raise ValueError("restart cannot also edit previous exclusions")
        return self


class PlaceTarget(PlanningModel):
    kind: Literal["name", "selected", "ordinal", "all"]
    # A literal span from the latest query, never a generated key or screen index.
    text: str = Field(min_length=1, max_length=200)


class PlaceEdit(PlanningModel):
    operation: Literal["exclude", "restore"]
    operation_quote: str = Field(min_length=1, max_length=500)
    targets: tuple[PlaceTarget, ...] = Field(min_length=1, max_length=120)


class BookmarkEdit(PlanningModel):
    operation: Literal["save", "remove"]
    # One explicit command clause, not a model-authored description.
    operation_quote: str = Field(min_length=1, max_length=500)
    target: PlaceTarget


Interpretation.model_rebuild()


class PendingDecision(PlanningModel):
    # A separate tool call cannot manufacture or replace a filter on acceptance.
    decision: Literal["accept", "reject", "revise", "new_request", "unclear"]
