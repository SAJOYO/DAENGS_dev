"""Gateway envelopes. Place exclusively owns the nested filter/operation schemas."""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, model_validator

from daengs_backend.schemas.facility_discovery import InputModel


class FilterEditRequest(InputModel):
    client_request_id: UUID
    base_revision: StrictInt = Field(ge=0, le=2**53 - 2)
    query: str = Field(min_length=1, max_length=1000)
    base_state: dict[str, Any]

    @model_validator(mode="after")
    def bounded(self):
        if not self.query.strip() or len(json.dumps(self.base_state).encode()) > 32 * 1024:
            raise ValueError("invalid filter edit input")
        return self


class FilterEditAction(InputModel):
    client_request_id: UUID
    search_id: UUID
    expected_revision: StrictInt = Field(ge=1)
    current_state: dict[str, Any]
    confirm_changes: StrictBool = False

    @model_validator(mode="after")
    def bounded(self):
        if len(json.dumps(self.current_state).encode()) > 32 * 1024:
            raise ValueError("filter state too large")
        return self


class CompiledFilterEdit(InputModel):
    status: Literal["ready", "needs_resolution"]
    base_state: dict[str, Any]
    proposed_state: dict[str, Any] | None
    proposal: dict[str, Any]
    requires_confirmation: StrictBool = False
    issues: list[dict[str, Any]]

    @model_validator(mode="after")
    def consistency(self):
        if self.status == "ready" and (self.proposed_state is None or self.requires_confirmation):
            raise ValueError("invalid ready proposal")
        if self.requires_confirmation and self.proposed_state is None:
            raise ValueError("no proposal to confirm")
        return self


class FilterEditResponse(InputModel):
    contract_version: Literal["place-filter-edit-v1"] = "place-filter-edit-v1"
    search_id: UUID
    revision: StrictInt = Field(ge=1)
    request: FilterEditRequest
    compiled: CompiledFilterEdit
    expires_at: datetime
    action_request: FilterEditAction | None = None
    result: dict[str, Any] | None = None
