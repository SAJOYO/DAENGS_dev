"""Latest generation of source scenes, independent of user-written diary content."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class WalkStoryboard(Base):
    __tablename__ = "walk_storyboards"
    __table_args__ = (
        CheckConstraint("generation > 0", name="walk_storyboards_generation_check"),
        CheckConstraint(
            "status IN ('running','ready','failed')", name="walk_storyboards_status_check"
        ),
    )
    walk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("walks.id", ondelete="CASCADE"), primary_key=True
    )
    generation: Mapped[int] = mapped_column(BigInteger)
    input_revision: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bundle: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))
