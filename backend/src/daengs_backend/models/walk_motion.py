"""Optional backup sidecar; legacy Walk queries do not load these tables."""

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class WalkMotionBackup(Base):
    __tablename__ = "walk_motion_backups"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(manifest) = 'object'", name="walk_motion_manifest_object"),
        CheckConstraint(
            "manifest_fingerprint ~ '^sha256:[0-9a-f]{64}$'", name="walk_motion_manifest_hash"
        ),
        CheckConstraint(
            "evidence_fingerprint ~ '^sha256:[0-9a-f]{64}$'", name="walk_motion_evidence_hash"
        ),
    )

    walk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("walks.id", ondelete="CASCADE"), primary_key=True
    )
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    manifest_fingerprint: Mapped[str] = mapped_column(String(71))
    evidence_fingerprint: Mapped[str | None] = mapped_column(String(71))


class WalkMotionChunk(Base):
    __tablename__ = "walk_motion_chunks"
    __table_args__ = (
        CheckConstraint("chunk_index BETWEEN 0 AND 390", name="walk_motion_chunk_index"),
        CheckConstraint(
            "jsonb_typeof(payload) = 'array' AND jsonb_array_length(payload) BETWEEN 1 AND 256",
            name="walk_motion_chunk_payload",
        ),
        CheckConstraint("fingerprint ~ '^sha256:[0-9a-f]{64}$'", name="walk_motion_chunk_hash"),
    )

    walk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("walk_motion_backups.walk_id", ondelete="CASCADE"), primary_key=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(String(71))
