from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from daengs_place.place.contracts import PlaceRef
from daengs_place.place.conversation.intent import Attribute, SearchPool, UnsupportedAttribute
from daengs_place.place.filters.contract import FilterState, Identifier
from daengs_place.place.filters.service import FilterResponse
from daengs_place.place.planning.contract import PlanningModel
from daengs_place.place.search import PlaceSearchRequest
from daengs_place.place.tools.contract import FilterChanges


class TurnPlan(PlanningModel):
    goal: Literal["show", "pick_one", "explain", "edit_only", "clarify"]
    changes: FilterChanges = Field(default_factory=FilterChanges)
    refresh: bool = False
    reference_index: int | None = Field(None, ge=1, le=120)
    question: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def coherent_goal(self) -> Self:
        if self.goal == "clarify" and not self.question.strip():
            raise ValueError("clarification needs a question")
        if self.goal in {"clarify", "explain"} and self.changes.model_dump(exclude_unset=True):
            raise ValueError("clarifying/explaining cannot also edit filters")
        if self.goal in {"edit_only", "clarify", "explain"} and self.refresh:
            raise ValueError("this goal cannot request a refresh")
        return self


class ResultSnapshot(PlanningModel):
    id: UUID
    fingerprint: str
    created_at: datetime
    result: FilterResponse
    display_order: tuple[PlaceRef, ...]
    exclusions: tuple[PlaceRef, ...] = Field(default=(), max_length=120)
    omitted: tuple[PlaceRef, ...] = Field(default=(), max_length=1640)
    pool_fingerprint: str = ""


class NamedPlace(PlanningModel):
    key: PlaceRef
    name: str


class ExplorationState(PlanningModel):
    known: tuple[NamedPlace, ...] = Field(default=(), max_length=120)
    excluded: tuple[NamedPlace, ...] = Field(default=(), max_length=120)
    presented: tuple[PlaceRef, ...] = Field(default=(), max_length=1200)
    fingerprint: str = ""


class DialogueTurn(PlanningModel):
    query: str = Field(max_length=1000)
    goal: str
    selected: PlaceRef | None = None


class PendingChange(PlanningModel):
    id: UUID
    revision: int = Field(ge=1)
    base_fingerprint: str
    pool: SearchPool = "all_places"
    base_pool: SearchPool = "all_places"
    original_query: str = Field(max_length=1000)
    question: str = Field(max_length=1000)
    candidate: FilterState
    goal: Literal["show", "pick_one", "edit_only"]
    refresh: bool = False
    unsupported: tuple[UnsupportedAttribute, ...] = ()
    expires_at: datetime


class SelectionBasis(PlanningModel):
    place: PlaceRef
    snapshot_id: UUID
    method: Literal["visible_order", "distance", "parking_then_distance", "user_reference"]


class AnswerFact(PlanningModel):
    attribute: Attribute
    status: Literal["known", "unknown", "unsupported"]
    value: bool | int | str | None = None
    source: PlaceRef | None = None
    as_of: str | None = None


class ConversationState(PlanningModel):
    search_pool: SearchPool = "all_places"
    filters: FilterState
    snapshot: ResultSnapshot | None = None
    selected: PlaceRef | None = None
    history: tuple[DialogueTurn, ...] = Field(default=(), max_length=6)
    pending_question: str = Field(default="", max_length=200)
    revision: int = Field(default=0, ge=0)
    pending_proposal: PendingChange | None = None
    selection_basis: SelectionBasis | None = None
    exploration: ExplorationState = Field(default_factory=ExplorationState)


class FilterRemoval(PlanningModel):
    search_pool: Literal["keep", "all_places", "unbookmarked", "new_candidates"] = "keep"
    # Deliberately narrower than model-proposed edits. Empty means search current filters.
    remove_all: tuple[Identifier, ...] = Field(default=(), max_length=8)
    remove_any: tuple[Identifier, ...] = Field(default=(), max_length=4)


class PrepareRequest(PlanningModel):
    mode: Literal["manual", "chat", "restore", "filters", "bootstrap"]
    saved_search: Literal["v1"] | None = None
    candidate_pools: Literal["v1"] | None = None
    restore_pool: SearchPool = "all_places"
    # Owner gateway reads a complete member snapshot; clients cannot supply these.
    bookmark_keys: tuple[PlaceRef, ...] | None = Field(None, max_length=200)
    restore_exploration: ExplorationState | None = None
    bookmark_commands: Literal["v1"] | None = None
    query: str = Field(default="", max_length=1000)
    manual: PlaceSearchRequest | None = None
    restore_filters: FilterState | None = None
    remove_filters: FilterRemoval | None = None
    previous: ConversationState | None = None
    # Set by the owner-bound gateway, never copied from an app-supplied state.
    base_revision: int | None = Field(None, ge=0)
    # IDs in the exact order the user saw, scoped to the saved snapshot.
    visible_order: tuple[PlaceRef, ...] = Field(default=(), max_length=120)
    visible_selected: PlaceRef | None = None

    @model_validator(mode="after")
    def valid_mode(self) -> Self:
        if self.mode == "bootstrap" and (
            self.previous is not None or self.query or self.manual is None
        ):
            raise ValueError("bootstrap creates empty search context from manual defaults")
        if self.mode == "filters":
            if (
                self.previous is None
                or self.remove_filters is None
                or self.manual is not None
                or self.query
            ):
                raise ValueError("filter removal requires a saved state and removal IDs only")
        elif self.remove_filters is not None:
            raise ValueError("removal IDs require filters mode")
        if self.mode == "restore":
            if self.restore_filters is None or self.previous or self.manual or self.query:
                raise ValueError("restore starts from validated filters, never previous results")
            policy = self.restore_filters.result_policy
            if policy.limit_per_kind > 20 or policy.uncertain_limit_per_kind != 0:
                raise ValueError("restore must respect the conversation result budget")
        elif (
            self.restore_filters is not None
            or self.restore_exploration is not None
            or self.restore_pool != "all_places"
        ):
            raise ValueError("saved filters require restore mode")
        if self.mode == "manual" and self.manual is None:
            raise ValueError("manual search requires filters")
        if self.mode == "chat" and (self.previous is None or not self.query.strip()):
            raise ValueError("chat requires a saved search and a query")
        return self


class BookmarkCommand(PlanningModel):
    key: PlaceRef
    name: str
    saved: bool


class ExecutionReceipt(PlanningModel):
    search_pool: SearchPool = "all_places"
    known_places: tuple[NamedPlace, ...] = ()
    feedback: str = "none"
    goal: str
    execution: Literal["not_run", "reused", "searched", "failed"]
    filters_changed: bool = False
    result_matches_filters: bool
    returned_count: int
    snapshot_id: UUID | None = None
    selected: PlaceRef | None = None
    code: str = ""
    question: str = ""
    # Allowed explanation statements, generated from actual result facts.
    evidence: dict[str, str] = Field(default_factory=dict)
    action: Literal[
        "execute", "await_confirmation", "clarify", "explain", "reject", "unsupported"
    ] = "execute"
    pending_id: UUID | None = None
    asked_attributes: tuple[Attribute, ...] = ()
    facts: tuple[AnswerFact, ...] = ()
    unsupported: tuple[UnsupportedAttribute, ...] = ()
    selection_basis: SelectionBasis | None = None
    browse: Literal["current", "next", "restart"] = "current"
    new_places: tuple[PlaceRef, ...] = ()
    excluded_places: tuple[NamedPlace, ...] = ()
    restored_places: tuple[NamedPlace, ...] = ()
    remaining: Literal["more", "exhausted", "unknown"] = "unknown"
    # Prepared command only. The member bookmark API has not executed it.
    bookmark_command: BookmarkCommand | None = None
    saved_search_filters: dict | None = None


class PreparedTurn(PlanningModel):
    state: ConversationState
    receipt: ExecutionReceipt


class AnswerRequest(PlanningModel):
    query: str = Field(max_length=1000)
    committed_revision: int = Field(ge=1)
    prepared: PreparedTurn


class AnswerDraft(PlanningModel):
    text: str = Field(min_length=1, max_length=500)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=12)


class ConversationAnswer(PlanningModel):
    text: str
    source: Literal["llm", "fallback"]
    evidence_ids: tuple[str, ...] = ()
    revision: int
