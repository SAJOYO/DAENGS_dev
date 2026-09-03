"""Public schemas for chat session and summary APIs (turn ingestion is not routed yet)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from daengs_backend.orchestration.contracts import AssistantResponse

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
    #: 그때 사용자에게 갔던 `AssistantResponse` 그대로 — 완료된 turn 에만 있습니다. 앱이
    #: 인용·handoff·clarify 를 다시 그릴 수 있게 `assistant_content` 와 별도로 둡니다.
    #: 저장 자체가 공개 계약만 담으므로(07_chats.sql) 프롬프트·예외·공급자 payload 는
    #: 여기로 나올 길이 없습니다.
    public_response: AssistantResponse | None
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
