from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from daengs_place.place.contracts import PlaceRef
from daengs_place.place.filters.contract import FilterState
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


class DialogueTurn(PlanningModel):
    query: str = Field(max_length=1000)
    goal: str
    selected: PlaceRef | None = None


class ConversationState(PlanningModel):
    filters: FilterState
    snapshot: ResultSnapshot | None = None
    selected: PlaceRef | None = None
    history: tuple[DialogueTurn, ...] = Field(default=(), max_length=6)
    pending_question: str = Field(default="", max_length=200)


class PrepareRequest(PlanningModel):
    mode: Literal["manual", "chat", "restore"]
    query: str = Field(default="", max_length=1000)
    manual: PlaceSearchRequest | None = None
    restore_filters: FilterState | None = None
    previous: ConversationState | None = None
    # IDs in the exact order the user saw, scoped to the saved snapshot.
    visible_order: tuple[PlaceRef, ...] = Field(default=(), max_length=120)
    visible_selected: PlaceRef | None = None

    @model_validator(mode="after")
    def valid_mode(self) -> Self:
        if self.mode == "restore":
            if self.restore_filters is None or self.previous or self.manual or self.query:
                raise ValueError("restore starts from validated filters, never previous results")
            policy = self.restore_filters.result_policy
            if policy.limit_per_kind > 20 or policy.uncertain_limit_per_kind != 0:
                raise ValueError("restore must respect the conversation result budget")
        elif self.restore_filters is not None:
            raise ValueError("saved filters require restore mode")
        if self.mode == "manual" and self.manual is None:
            raise ValueError("manual search requires filters")
        if self.mode == "chat" and (self.previous is None or not self.query.strip()):
            raise ValueError("chat requires a saved search and a query")
        return self


class ExecutionReceipt(PlanningModel):
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
