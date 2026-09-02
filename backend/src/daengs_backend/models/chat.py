"""Product chat persistence. The SQL source of truth is ``db/init/07_chats.sql``.

Chat persistence is product data, not observability logging (D-043).  Never copy
raw questions from these rows into logs or traces (D-037).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base

CHAT_PROCESSING_STATUSES = ("processing", "completed", "failed")
CHAT_SUMMARY_STATUSES = CHAT_PROCESSING_STATUSES


class ChatSession(Base):
    """A draft has no delivered assistant response and ``last_message_at`` is NULL."""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("chat_sessions_app_user_idx", "app_user_id"),
        Index("chat_sessions_pet_idx", "pet_id"),
        Index(
            "chat_sessions_one_draft_idx",
            "app_user_id",
            "pet_id",
            unique=True,
            postgresql_where=text("last_message_at IS NULL"),
        ),
        Index(
            "chat_sessions_active_order_idx",
            "app_user_id",
            "pet_id",
            text("last_message_at DESC"),
            text("id DESC"),
            postgresql_where=text("last_message_at IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="CASCADE")
    )
    pet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(120))
    agent_categories: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'")
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )


class ChatTurn(Base):
    """One user question and its delivered assistant response (or safe failure)."""

    __tablename__ = "chat_turns"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "client_message_id", name="chat_turns_session_client_key"
        ),
        CheckConstraint(
            "processing_status IN ('processing','completed','failed')",
            name="chat_turns_processing_status_check",
        ),
        CheckConstraint(
            "char_length(user_content) BETWEEN 1 AND 2000",
            name="chat_turns_user_content_length_check",
        ),
        CheckConstraint(
            "assistant_content IS NULL OR char_length(assistant_content) BETWEEN 1 AND 8000",
            name="chat_turns_assistant_content_length_check",
        ),
        CheckConstraint(
            "assistant_status IS NULL OR assistant_status IN "
            "('ANSWERED','PARTIAL','CLARIFY','HANDOFF','UNCERTAIN','REFUSED','PENDING','FAILED')",
            name="chat_turns_assistant_status_check",
        ),
        CheckConstraint(
            "(processing_status = 'processing' AND assistant_content IS NULL "
            "AND assistant_status IS NULL AND public_response IS NULL "
            "AND error_code IS NULL AND completed_at IS NULL) OR "
            "(processing_status = 'completed' AND assistant_content IS NOT NULL "
            "AND assistant_status IS NOT NULL AND request_id IS NOT NULL "
            "AND public_response IS NOT NULL AND jsonb_typeof(public_response) = 'object' "
            "AND error_code IS NULL "
            "AND completed_at IS NOT NULL) OR "
            "(processing_status = 'failed' AND assistant_content IS NULL "
            "AND assistant_status IS NULL AND public_response IS NULL "
            "AND error_code IS NOT NULL AND completed_at IS NOT NULL)",
            name="chat_turns_state_check",
        ),
        Index("chat_turns_session_order_idx", "session_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    client_message_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    processing_status: Mapped[str] = mapped_column(String(20))
    user_content: Mapped[str] = mapped_column(Text)
    assistant_content: Mapped[str | None] = mapped_column(Text)
    assistant_status: Mapped[str | None] = mapped_column(String(20))
    request_id: Mapped[str | None] = mapped_column(String(64))
    agent_categories: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'")
    )
    public_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    processing_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )


class ChatSummary(Base):
    """A reserved, completed, or failed user-requested summary."""

    __tablename__ = "chat_summaries"
    __table_args__ = (
        UniqueConstraint(
            "app_user_id",
            "client_request_id",
            name="chat_summaries_user_request_key",
        ),
        CheckConstraint(
            "processing_status IN ('processing','completed','failed')",
            name="chat_summaries_processing_status_check",
        ),
        CheckConstraint(
            "source_turn_count BETWEEN 1 AND 30",
            name="chat_summaries_source_turn_count_check",
        ),
        CheckConstraint(
            "(processing_status = 'processing' AND title IS NULL "
            "AND question_summary IS NULL AND answer_summary IS NULL "
            "AND key_points IS NULL AND cautions IS NULL AND source_citations IS NULL "
            "AND model IS NULL AND prompt_version IS NULL AND error_code IS NULL "
            "AND completed_at IS NULL) OR "
            "(processing_status = 'completed' AND title IS NOT NULL "
            "AND question_summary IS NOT NULL AND answer_summary IS NOT NULL "
            "AND key_points IS NOT NULL AND cautions IS NOT NULL "
            "AND source_citations IS NOT NULL AND jsonb_typeof(source_citations) = 'array' "
            "AND model IS NOT NULL "
            "AND prompt_version IS NOT NULL AND error_code IS NULL "
            "AND completed_at IS NOT NULL) OR "
            "(processing_status = 'failed' AND title IS NULL "
            "AND question_summary IS NULL AND answer_summary IS NULL "
            "AND key_points IS NULL AND cautions IS NULL AND source_citations IS NULL "
            "AND model IS NULL AND prompt_version IS NULL AND error_code IS NOT NULL "
            "AND completed_at IS NOT NULL)",
            name="chat_summaries_state_check",
        ),
        Index(
            "chat_summaries_source_reservation_idx",
            "source_session_id",
            "source_turn_count",
            unique=True,
            postgresql_where=text(
                "source_session_id IS NOT NULL AND "
                "processing_status IN ('processing','completed')"
            ),
        ),
        Index(
            "chat_summaries_scope_order_idx",
            "app_user_id",
            "pet_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index("chat_summaries_pet_idx", "pet_id"),
        Index("chat_summaries_source_session_idx", "source_session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="CASCADE")
    )
    pet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="CASCADE")
    )
    source_session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("chat_sessions.id", ondelete="SET NULL")
    )
    source_turn_count: Mapped[int] = mapped_column(Integer)
    client_request_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    processing_status: Mapped[str] = mapped_column(String(20))
    title: Mapped[str | None] = mapped_column(String(120))
    question_summary: Mapped[str | None] = mapped_column(Text)
    answer_summary: Mapped[str | None] = mapped_column(Text)
    key_points: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    cautions: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    source_citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    agent_categories: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'")
    )
    model: Mapped[str | None] = mapped_column(String(60))
    prompt_version: Mapped[str | None] = mapped_column(String(60))
    error_code: Mapped[str | None] = mapped_column(String(64))
    processing_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
