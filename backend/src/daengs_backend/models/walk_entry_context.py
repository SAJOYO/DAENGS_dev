"""SQL-owned durable context jobs and append-only collection attempts."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class WalkEntryContextJob(Base):
    __tablename__ = "walk_entry_context_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["walk_id", "entry_id"], ["walk_entries.walk_id", "walk_entries.id"], ondelete="CASCADE"
        ),
        UniqueConstraint("walk_id", "entry_id", "revision", "policy_version", "tag"),
        CheckConstraint("revision > 0"),
        CheckConstraint("attempts BETWEEN 0 AND 3"),
        CheckConstraint("collection_round >= 0"),
        CheckConstraint(
            "tag IN ('space.facility', 'space.park', 'space.river', 'environment.weather', 'space.address', 'space.commerce')"
        ),
        CheckConstraint("state IN ('pending', 'running', 'completed', 'failed', 'cancelled')"),
        CheckConstraint(
            "(state = 'running') = (lease_token IS NOT NULL AND lease_until IS NOT NULL)"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    walk_id: Mapped[uuid.UUID]
    entry_id: Mapped[uuid.UUID]
    revision: Mapped[int] = mapped_column(Integer)
    policy_version: Mapped[str] = mapped_column(String)
    tag: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String)
    attempts: Mapped[int] = mapped_column(Integer)
    collection_round: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    backfill_policy: Mapped[str | None] = mapped_column(String)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WalkEntryContextEnvelope(Base):
    __tablename__ = "walk_entry_context_envelopes"
    __table_args__ = (
        UniqueConstraint("job_id", "collection_round", "attempt"),
        CheckConstraint("collection_round >= 0"),
        CheckConstraint("attempt BETWEEN 1 AND 3"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("walk_entry_context_jobs.id", ondelete="CASCADE")
    )
    attempt: Mapped[int] = mapped_column(Integer)
    collection_round: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    envelope: Mapped[dict] = mapped_column(JSONB)
