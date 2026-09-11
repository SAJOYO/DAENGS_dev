"""One versioned publisher manifest per walk, removed by the walk's account cascade."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class WalkPhotoManifest(Base):
    __tablename__ = "walk_photo_manifests"
    __table_args__ = (
        CheckConstraint("revision > 0", name="walk_photo_revision_positive"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="walk_photo_hash_valid"),
        CheckConstraint(
            "jsonb_typeof(records) = 'array' AND jsonb_array_length(records) <= 200",
            name="walk_photo_records_bounded",
        ),
    )
    walk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("walks.id", ondelete="CASCADE"), primary_key=True
    )
    publisher_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    revision: Mapped[int] = mapped_column(Integer)
    request_hash: Mapped[str] = mapped_column(String(64))
    records: Mapped[list] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
