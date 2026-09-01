"""대화 기록과 저장된 AI 요약. 원본 스키마는 `db/init/07_chats.sql` 입니다.

Alembic 을 쓰지 않으므로 **SQL 을 고쳤으면 이 파일도 손으로 맞춰야 합니다**
(CLAUDE.md · D-011).

`agent_categories` 에 들어가는 것은 라우팅이 낸 능력 이름 그대로입니다 —
`training` · `life` · `walk` (`orchestration/contracts.py` `CapabilityName`).
**한글 배지 라벨(훈련 · 생활 · 제도)로 접어서 저장하지 않습니다.** 라벨은 앱의
어휘라 바뀔 수 있고, 그때 이미 쌓인 행을 전부 고치게 됩니다.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base

CHAT_MESSAGE_ROLES = ("user", "assistant")


class ChatSession(Base):
    """대화 한 묶음. 사용자+강아지마다 5개까지만 남습니다.

    상한을 지키는 곳은 `services/chat.py` 입니다 — DB 는 행 수를 셀 수 없고,
    트리거로 지우면 삭제가 조용히 일어나 앱이 이유를 못 봅니다.
    """

    __tablename__ = "chat_sessions"

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

    #: 이 세션에 관여한 능력들의 합집합. 메시지마다의 값은 `ChatMessage` 에 있습니다.
    agent_categories: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    #: 목록 정렬 기준이자 5개 유지에서 **가장 오래된 것**을 고르는 기준입니다.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<ChatSession {self.id} title={self.title!r}>"


class ChatMessage(Base):
    """세션 안의 말 한 마디. **세션이 사라지면 같이 사라집니다.**"""

    __tablename__ = "chat_messages"

    __table_args__ = (
        CheckConstraint(
            "role IN ('user','assistant')", name="chat_messages_role_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )

    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)

    #: 이 답을 만든 능력들. 사용자 메시지는 빈 배열입니다.
    agent_categories: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'")
    )

    #: 오케스트레이션 최상위 상태. **실패한 답을 완료된 답처럼 저장하지 않기 위해**
    #: 같이 남깁니다 (`AssistantStatus`).
    assistant_status: Mapped[str | None] = mapped_column(String(20))

    request_id: Mapped[str | None] = mapped_column(String(64))

    #: 기기가 만든 멱등 키. 같은 값이면 두 번 눌러도 한 번만 들어갑니다.
    client_message_id: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<ChatMessage {self.id} role={self.role}>"


class ChatSummary(Base):
    """사용자가 눌러서 저장한 AI 대화 요약. 보관함에 뜹니다.

    **원본 대화와 별개의 기록입니다.** `session_id` 가 nullable 인 이유가 그것으로,
    5개 유지가 원본을 밀어내면 `ON DELETE SET NULL` 로 비워질 뿐 요약은 남습니다.
    """

    __tablename__ = "chat_summaries"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    app_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="CASCADE")
    )
    pet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="CASCADE")
    )

    #: 원본이 사라지면 NULL 입니다. 앱은 비었으면 "원본 대화 없음"으로 보여 줍니다.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("chat_sessions.id", ondelete="SET NULL")
    )

    title: Mapped[str] = mapped_column(String(120))
    question_summary: Mapped[str] = mapped_column(Text)
    answer_summary: Mapped[str] = mapped_column(Text)

    #: **원문의 주의·한계·출처를 접지 않습니다.** 요약이 경고를 떨어뜨리면
    #: 보관함에 남는 것이 원문보다 위험한 문장이 됩니다.
    key_points: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'")
    )
    cautions: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'")
    )
    source_citations: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'")
    )

    agent_categories: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'")
    )

    model: Mapped[str] = mapped_column(String(60))
    prompt_version: Mapped[str] = mapped_column(String(60))
    source_message_count: Mapped[int] = mapped_column(Integer)

    #: 요약 버튼을 두 번 눌러도 한 건만 남게 하는 키. 생성이 유료 호출이라
    #: 메시지보다 이쪽이 더 중요합니다.
    client_request_id: Mapped[str] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<ChatSummary {self.id} title={self.title!r}>"
