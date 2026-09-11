"""Additive v2 sidecar and operation receipts; original walk_entries stays compatible."""

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class WalkEntryPin(Base):
    __tablename__ = "walk_entry_pins"
    __table_args__ = (
        ForeignKeyConstraint(
            ["walk_id", "entry_id"], ["walk_entries.walk_id", "walk_entries.id"], ondelete="CASCADE"
        ),
        CheckConstraint("pin_revision >= 0", name="walk_entry_pins_revision"),
    )
    walk_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    pin_revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)


class WalkEntryMutation(Base):
    __tablename__ = "walk_entry_mutations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["walk_id", "entry_id"], ["walk_entries.walk_id", "walk_entries.id"], ondelete="CASCADE"
        ),
    )
    walk_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    mutation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
