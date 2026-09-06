"""수정 가능한 산책 기록. 삭제 표식에는 본문과 위치를 남기지 않는다."""

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class WalkEntry(Base):
    __tablename__ = "walk_entries"
    __table_args__ = (CheckConstraint("revision > 0", name="walk_entries_revision"),)

    walk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("walks.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    mutation_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
