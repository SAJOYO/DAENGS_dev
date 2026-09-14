"""Sealed outputs, separate from mutable diary scenes and legacy walk queries."""

import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class WalkMeasurement(Base):
    __tablename__ = "walk_measurements"
    __table_args__ = (
        UniqueConstraint("walk_id", "input_key", name="walk_measurement_input"),
        CheckConstraint("measurement_id ~ '^shadow-[0-9a-f]{64}$'", name="walk_measurement_id"),
        CheckConstraint("input_key ~ '^[0-9a-f]{64}$'", name="walk_measurement_input_hash"),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="walk_measurement_hash"),
    )
    walk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("walks.id", ondelete="CASCADE"), primary_key=True
    )
    measurement_id: Mapped[str] = mapped_column(String(71), primary_key=True)
    input_key: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))


class WalkMeasurementChunk(Base):
    __tablename__ = "walk_measurement_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["walk_id", "measurement_id"],
            ["walk_measurements.walk_id", "walk_measurements.measurement_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("chunk_index BETWEEN 0 AND 1999", name="walk_measurement_chunk_index"),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="walk_measurement_chunk_hash"),
    )
    walk_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    measurement_id: Mapped[str] = mapped_column(String(71), primary_key=True)
    chunk_index: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
