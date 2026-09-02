"""Public schemas for chat session and summary APIs (turn ingestion is not routed yet)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AgentCategory = Literal["training", "life", "walk"]


class ChatSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pet_id: uuid.UUID
    title: str | None = Field(default=None, max_length=120)


class ChatTurnResponse(BaseModel):
    id: uuid.UUID
    client_message_id: uuid.UUID
    processing_status: Literal["processing", "completed", "failed"]
    user_content: str
    assistant_content: str | None
    agent_categories: list[AgentCategory]
    assistant_status: str | None
    error_code: str | None
    completed_at: datetime | None
    created_at: datetime


class ChatSessionResponse(BaseModel):
    id: uuid.UUID
    pet_id: uuid.UUID
    title: str
    agent_categories: list[AgentCategory]
    created_at: datetime
    last_message_at: datetime | None


class ChatSessionDetailResponse(BaseModel):
    session: ChatSessionResponse
    turns: list[ChatTurnResponse]


class ChatSessionListResponse(BaseModel):
    sessions: list[ChatSessionResponse]
    max_sessions: int


class ChatSummaryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: uuid.UUID


class ChatSummaryResponse(BaseModel):
    id: uuid.UUID
    pet_id: uuid.UUID
    source_session_id: uuid.UUID | None
    source_turn_count: int
    title: str
    question_summary: str
    answer_summary: str
    key_points: list[str]
    cautions: list[str]
    source_citations: list[dict[str, str | None]]
    agent_categories: list[AgentCategory]
    model: str
    prompt_version: str
    completed_at: datetime
    created_at: datetime


class ChatSummaryListResponse(BaseModel):
    summaries: list[ChatSummaryResponse]


__all__ = [
    "AgentCategory",
    "ChatSessionCreate",
    "ChatSessionDetailResponse",
    "ChatSessionListResponse",
    "ChatSessionResponse",
    "ChatSummaryCreate",
    "ChatSummaryListResponse",
    "ChatSummaryResponse",
    "ChatTurnResponse",
]
