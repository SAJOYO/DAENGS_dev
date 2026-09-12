"""Model-owned meaning, without filter IDs, executable actions or answer prose."""

from typing import Literal, Self

from pydantic import Field, StrictBool, model_validator

from daengs_place.place.filters.contract import KindList
from daengs_place.place.name_query import PlaceNameQuery
from daengs_place.place.planning.contract import PlanningModel

SearchPool = Literal["all_places", "bookmarks", "unbookmarked", "new_candidates"]
FacilityKind = Literal["facility_action", "facility_state", "needs_input", "out_of_scope"]

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
    # None is for deterministic internal callers and historical research replays.
    # The live provider validates ScopedInterpretation, where these fields are required.
    kind: FacilityKind | None = None
    request_quote: str = Field(default="", max_length=1000)
    state_subject: Literal["place", "filters"] = "place"
    goal: Literal["show", "pick_one", "explain", "edit_only", "clarify"]
    search_scope: Literal["keep", "bookmarks", "all_places", "unbookmarked", "new_candidates"] = (
        Field(
            default="keep",
            description="후보 집합 변경만 표현한다. 검색 동사나 찜 저장 행위의 부정은 집합 변경이 아니다.",
        )
    )
    search_scope_quote: str = Field(
        default="",
        max_length=500,
        description="검색 대상 집합을 바꾸라는 최신 발화의 원문 구절. 저장 행위의 부정은 근거가 아니며 빈 문자열이다.",
    )
    spatial_scope: Literal["keep", "unbounded"] = "keep"
    navigation: Literal["stay", "restore_search"] = Field(
        default="stay",
        description="이전 검색 화면으로 돌아가기만 restore_search. 찜 제한 해제는 검색 변경이다.",
    )
    forbid_save: StrictBool = Field(
        default=False,
        description="이번 요청에서 저장하지 말라는 뜻. 찜 해제나 검색 집합 변경이 아니다.",
    )
    feedback: Literal["none", "evaluation", "familiarity", "information_dispute"] = "none"
    search_request_quote: str = Field(
        default="",
        max_length=500,
        description="피드백과 함께 말한 실제 검색·조건 변경 요청의 최신 원문 구절. 불만만 있으면 빈 문자열",
    )
    familiarity: "FamiliarityCorrection | None" = None
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

    @model_validator(mode="before")
    @classmethod
    def derive_feedback(cls, value):
        if (
            isinstance(value, dict)
            and value.get("familiarity")
            and value.get("feedback", "none") == "none"
        ):
            return {**value, "feedback": "familiarity"}
        return value

    @model_validator(mode="after")
    def exploration_goal(self) -> Self:
        if (self.browse != "current" or self.place_edit) and self.goal != "show":
            raise ValueError("exploration edits require show")
        if self.familiarity is not None and self.feedback not in {"none", "familiarity"}:
            raise ValueError("conflicting familiarity and feedback")
        if self.browse == "restart" and (self.place_edit or self.familiarity):
            raise ValueError("restart cannot also edit previous exclusions")
        return self


class PlaceTarget(PlanningModel):
    kind: Literal["name", "selected", "ordinal", "all"]
    # A literal span from the latest query, never a generated key or screen index.
    text: str = Field(min_length=1, max_length=200)


class FamiliarityCorrection(PlanningModel):
    quote: str = Field(
        min_length=1,
        max_length=500,
        description="대상과 이미 안다는 진술을 포함한 최신 원문 절 전체",
    )
    targets: tuple[PlaceTarget, ...] = Field(min_length=1, max_length=120)


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


class ScopedInterpretation(Interpretation):
    kind: FacilityKind
    request_quote: str = Field(max_length=1000)

    @model_validator(mode="after")
    def bounded_authority(self) -> Self:
        mutations = (
            self.changes != SemanticChanges()
            or self.search_scope != "keep"
            or self.spatial_scope != "keep"
            or self.navigation != "stay"
            or self.bookmark
            or self.place_edit
            or self.familiarity
            or self.refresh
            or self.browse != "current"
        )
        if self.kind == "out_of_scope":
            if (
                self.goal != "clarify"
                or mutations
                or self.request_quote
                or self.feedback != "none"
                or self.reference_index is not None
                or self.asked_attributes
                or self.unsupported
                or self.region_query
                or self.search_request_quote
                or self.search_scope_quote
                or self.forbid_save
                or self.unresolved != "none"
                or self.state_subject != "place"
            ):
                raise ValueError("out-of-scope input has no facility proposal authority")
        elif not self.request_quote.strip():
            raise ValueError("a facility request needs literal evidence")
        elif self.kind == "facility_state":
            if (
                self.goal != "explain"
                or mutations
                or self.region_query
                or self.unresolved != "none"
            ):
                raise ValueError("state questions cannot mutate facilities")
            if self.state_subject == "filters" and (self.asked_attributes or self.reference_index):
                raise ValueError("filter questions cannot also select a place")
        elif self.kind == "needs_input":
            if self.goal != "clarify" or self.unresolved == "none" or mutations:
                raise ValueError("clarification needs a missing-item code, without mutations")
        elif self.goal not in {"show", "pick_one", "edit_only"}:
            raise ValueError("facility action requires an action goal")
        if self.kind != "facility_state" and self.state_subject != "place":
            raise ValueError("filter state is read-only")
        return self


class PendingDecision(PlanningModel):
    # A separate tool call cannot manufacture or replace a filter on acceptance.
    decision: Literal["accept", "reject", "revise", "new_request", "unclear", "out_of_scope"]
