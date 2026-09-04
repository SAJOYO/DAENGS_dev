"""AI 답변 신고. SQL 원본은 ``db/init/10_answer_reports.sql`` 입니다.

**답변 원문은 여기 없습니다.** `turn_id` 가 `chat_turns` 한 행을 가리키고, 질문·답변·
근거는 전부 거기 있습니다 (D-048). 신고 표에 복사하면 탈퇴 시 파기 대상이 둘이 됩니다.

열람 범위는 D-053 이 정했습니다 — **신고된 turn 하나**만 열고, 그 대화의 다른 부분은
열지 않습니다. 그래서 이 모델에 세션을 직접 가리키는 열이 없습니다.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base

#: `status` 에 들어가는 값. SQL 의 CHECK 와 같은 내용이니 한쪽만 고치지 마세요.
ANSWER_REPORT_STATUSES = ("open", "reviewed", "dismissed")

#: 신고 사유 길이 상한. SQL 의 CHECK 와 같습니다.
REASON_MAX_CHARS = 500


class AnswerReport(Base):
    """신고 한 건. 처리 전에는 `reviewed_by` · `reviewed_at` 이 둘 다 비어 있습니다."""

    __tablename__ = "answer_reports"
    __table_args__ = (
        Index("answer_reports_created_idx", text("created_at DESC"), text("id DESC")),
        Index(
            "answer_reports_status_created_idx",
            "status",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index("answer_reports_turn_idx", "turn_id"),
        # 같은 답변을 **여러 사람이** 신고하는 것은 막지 않습니다 — 몇 번 신고됐는지가
        # 그 답변이 얼마나 나쁜지의 신호입니다. 막는 것은 한 사람의 중복뿐입니다.
        UniqueConstraint("turn_id", "app_user_id", name="answer_reports_turn_user_key"),
        CheckConstraint(
            "status IN ('open', 'reviewed', 'dismissed')",
            name="answer_reports_status_check",
        ),
        CheckConstraint(
            "char_length(reason) BETWEEN 1 AND 500",
            name="answer_reports_reason_length_check",
        ),
        CheckConstraint(
            "(status = 'open' AND reviewed_by IS NULL AND reviewed_at IS NULL)"
            " OR (status <> 'open' AND reviewed_by IS NOT NULL"
            " AND reviewed_at IS NOT NULL)",
            name="answer_reports_review_state_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    #: **CASCADE 입니다.** 탈퇴로 대화가 지워지면 신고도 함께 사라집니다 (D-053).
    turn_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chat_turns.id", ondelete="CASCADE"), nullable=False
    )
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False
    )

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'open'")
    )

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
