"""Read model for record-scoped context, separate from the unchanged entry wire contract."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field, JsonValue


class ContextSource(BaseModel):
    tag: Literal[
        "space.facility", "space.park", "space.river", "environment.weather", "space.address"
    ]
    state: Literal["pending", "running", "completed", "failed", "cancelled"]
    attempts: int = Field(ge=0, le=3)
    failure_reason: Literal["attempts_exhausted"] | None = None
    envelope: dict[str, JsonValue] | None = None


class EntryContexts(BaseModel):
    entry_id: uuid.UUID
    revision: int = Field(gt=0)
    status: Literal["disabled", "tracked", "not_requested"]
    sources: list[ContextSource]
