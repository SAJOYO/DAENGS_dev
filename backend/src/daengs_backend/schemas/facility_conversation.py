"""Conversation gateway envelopes. Place owns filter and result semantics."""

from typing import Any, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from daengs_backend.schemas.facility_discovery import InputModel


class ConversationRequest(InputModel):
    client_request_id: UUID
    session_id: UUID | None = None
    expected_revision: int = Field(default=0, ge=0)
    mode: Literal["manual", "chat"]
    query: str = Field(default="", max_length=1000)
    manual: dict[str, Any] | None = None
    visible_order: list[dict[str, str]] = Field(default_factory=list, max_length=120)
    visible_selected: dict[str, str] | None = None

    @model_validator(mode="after")
    def required_context(self) -> Self:
        if self.mode == "chat" and (
            self.session_id is None or not self.query.strip() or self.manual is not None
        ):
            raise ValueError("chat requires an existing session and query, not replacement filters")
        if self.mode == "manual" and self.manual is None:
            raise ValueError("manual search requires filter input")
        if self.session_id is None and self.expected_revision != 0:
            raise ValueError("new session revision must be zero")
        return self


class ConversationResponse(InputModel):
    contract_version: Literal["facility-conversation-v1"] = "facility-conversation-v1"
    session_id: UUID
    revision: int
    client_request_id: UUID
    filters: dict[str, Any]
    search: dict[str, Any] | None
    selected: dict[str, str] | None
    display_order: list[dict[str, str]]
    receipt: dict[str, Any]
    answer: dict[str, Any] | None = None
