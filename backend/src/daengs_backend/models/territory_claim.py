"""Shared occupancy, independent of immutable photo visits. SQL: 20_territory_claims.sql."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from daengs_backend.models.base import Base


class TerritoryClaimSession(Base):
    __tablename__ = "territory_claim_sessions"
    __table_args__ = (
        UniqueConstraint("app_user_id", "client_session_id"),
        CheckConstraint("phase IN ('RECORDING','PAUSED','ENDED')"),
        CheckConstraint("version >= 0"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    app_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"))
    client_session_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    pet_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    phase: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(BigInteger)


class TerritoryClaimSite(Base):
    __tablename__ = "territory_claim_sites"
    __table_args__ = (CheckConstraint("version >= 0"),)
    site_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger)


class TerritoryClaim(Base):
    __tablename__ = "territory_claims"
    __table_args__ = (
        Index("territory_claims_pet_idx", "pet_id"),
        UniqueConstraint("session_id", "site_id"),
        CheckConstraint("expected_site_version >= 0"),
        CheckConstraint(
            "disposition IN ('GRANTED','PHOTO_REQUIRED','POLICY_UNDECIDED','ALREADY_OWNED')"
        ),
        CheckConstraint(
            "photo_status IN ('NOT_SUBMITTED','PENDING','VERIFIED','REJECTED','RETRY_PENDING')"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("territory_claim_sessions.id", ondelete="CASCADE")
    )
    site_id: Mapped[str] = mapped_column(ForeignKey("territory_claim_sites.site_id"))
    pet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"))
    expected_site_version: Mapped[int] = mapped_column(BigInteger)
    disposition: Mapped[str] = mapped_column(String(24))
    photo_status: Mapped[str] = mapped_column(String(24))
    current_photo_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("territory_attempts.id", ondelete="SET NULL")
    )
    resolution_code: Mapped[str | None] = mapped_column(String(40))
    # Original request evidence is retained for exact retry comparison.
    contact: Mapped[str] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TerritoryOccupancy(Base):
    __tablename__ = "territory_occupancies"
    __table_args__ = (CheckConstraint("certification IN ('UNVERIFIED','VERIFIED')"),)
    site_id: Mapped[str] = mapped_column(
        ForeignKey("territory_claim_sites.site_id"), primary_key=True
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("territory_claims.id", ondelete="CASCADE"), unique=True
    )
    certification: Mapped[str] = mapped_column(String(16))
    occupied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TerritoryClaimPhoto(Base):
    __tablename__ = "territory_claim_photos"
    photo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("territory_attempts.id", ondelete="CASCADE"), primary_key=True
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("territory_claims.id", ondelete="CASCADE"), index=True
    )
