"""Conversation gateway envelopes. Place owns filter and result semantics."""

from typing import Any, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from daengs_backend.schemas.facility_discovery import InputModel


class ConversationRequest(InputModel):
    client_request_id: UUID
    session_id: UUID | None = None
    expected_revision: int = Field(default=0, ge=0)
    mode: Literal["manual", "chat", "restore", "filters"]
    bookmark_commands: Literal["v1"] | None = None
    saved_search: Literal["v1"] | None = None
    candidate_pools: Literal["v1"] | None = None
    restore_pool: Literal["all_places", "unbookmarked", "new_candidates"] = "all_places"
    source_session_id: UUID | None = None
    source_revision: int | None = Field(None, ge=1)
    query: str = Field(default="", max_length=1000)
    manual: dict[str, Any] | None = None
    restore_filters: dict[str, Any] | None = None
    remove_filters: dict[str, Any] | None = None
    visible_order: list[dict[str, str]] = Field(default_factory=list, max_length=120)
    visible_selected: dict[str, str] | None = None

    @model_validator(mode="after")
    def required_context(self) -> Self:
        if (self.source_session_id is None) != (self.source_revision is None):
            raise ValueError("restore source requires session and revision")
        if self.mode != "restore" and (self.source_session_id or self.restore_pool != "all_places"):
            raise ValueError("only restore accepts a source or pool")
        if (
            self.restore_pool != "all_places" or self.source_session_id
        ) and self.candidate_pools != "v1":
            raise ValueError("candidate pool capability required")
        if self.mode == "filters":
            if (
                self.session_id is None
                or self.remove_filters is None
                or self.manual is not None
                or self.query
            ):
                raise ValueError("filter removal requires an existing session and removal IDs only")
        elif self.remove_filters is not None:
            raise ValueError("only filters mode accepts removal IDs")
        if self.mode == "chat" and (
            self.session_id is None or not self.query.strip() or self.manual is not None
        ):
            raise ValueError("chat requires an existing session and query, not replacement filters")
        if self.mode == "manual" and self.manual is None:
            raise ValueError("manual search requires filter input")
        if self.mode == "restore":
            if (
                self.session_id is not None
                or self.restore_filters is None
                or self.manual
                or self.query
            ):
                raise ValueError("restore requires only filters in a new session")
        elif self.restore_filters is not None:
            raise ValueError("only restore accepts saved filters")
        if self.session_id is None and self.expected_revision != 0:
            raise ValueError("new session revision must be zero")
        return self


class ConversationRecoveryRequest(InputModel):
    session_id: UUID | None = None
    client_request_id: UUID


class ConversationAnswerRequest(InputModel):
    session_id: UUID
    client_request_id: UUID
    revision: int = Field(ge=1)


class ConversationResponse(InputModel):
    contract_version: Literal["facility-conversation-v2"] = "facility-conversation-v2"
    session_id: UUID
    revision: int
    client_request_id: UUID
    search_pool: Literal["all_places", "unbookmarked", "new_candidates"] = "all_places"
    excluded_keys: list[dict[str, str]] = Field(default_factory=list, max_length=120)
    filters: dict[str, Any]
    search: dict[str, Any] | None
    selected: dict[str, str] | None
    display_order: list[dict[str, str]]
    receipt: dict[str, Any]
    answer: dict[str, Any] | None = None
    answer_status: Literal["none", "pending", "ready"] = "none"
