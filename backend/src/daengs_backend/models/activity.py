"""DEV activity schema; authoritative SQL is db/init/21_activity_game.sql."""

import uuid

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from daengs_backend.models.base import Base


class ActivitySessionLink(Base):
    __tablename__ = "activity_session_links"
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), primary_key=True
    )
    client_session_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    walk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("walks.id", ondelete="SET NULL"), unique=True
    )
    game_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("territory_claim_sessions.id", ondelete="SET NULL"), unique=True
    )


class ActivityWalkHead(Base):
    __tablename__ = "activity_walk_heads"
    __table_args__ = (
        CheckConstraint("revision > 0"),
        CheckConstraint("processed_revision >= 0"),
        CheckConstraint("processed_revision <= revision"),
    )
    walk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("walks.id", ondelete="CASCADE"), primary_key=True
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("walk_analyses.id", ondelete="CASCADE"), unique=True
    )
    revision: Mapped[int] = mapped_column(BigInteger)
    processed_revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    processed_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("walk_analyses.id", ondelete="SET NULL")
    )
    contribution: Mapped[dict | None] = mapped_column(JSONB)


class ActivitySeason(Base):
    __tablename__ = "activity_seasons"
    __table_args__ = (
        CheckConstraint("btrim(id) <> ''"),
        CheckConstraint("starts_ms >= 0"),
        CheckConstraint("ends_ms > starts_ms"),
        CheckConstraint("revision >= 0"),
        CheckConstraint("status IN ('ACTIVE','FINALIZED')"),
        CheckConstraint("coverage_start_ms >= starts_ms AND coverage_start_ms < ends_ms"),
        CheckConstraint("confirmed_ms >= coverage_start_ms AND confirmed_ms <= ends_ms"),
        Index(
            "activity_one_active_season",
            "status",
            unique=True,
            postgresql_where=text("status='ACTIVE'"),
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    starts_ms: Mapped[int] = mapped_column(BigInteger)
    ends_ms: Mapped[int] = mapped_column(BigInteger)
    coverage_start_ms: Mapped[int] = mapped_column(BigInteger)
    confirmed_ms: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String)
    rules: Mapped[dict] = mapped_column(JSONB)


class ActivityAccount(Base):
    __tablename__ = "activity_accounts"
    __table_args__ = (
        CheckConstraint("revision > 0"),
        CheckConstraint("processed_revision >= 0"),
        CheckConstraint("processed_revision <= revision"),
        CheckConstraint("final_rank > 0", name="activity_final_rank_positive"),
    )
    season_id: Mapped[str] = mapped_column(
        ForeignKey("activity_seasons.id", ondelete="CASCADE"), primary_key=True
    )
    pet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pets.id", ondelete="CASCADE"), primary_key=True
    )
    score: Mapped[dict] = mapped_column(JSONB)
    final_score: Mapped[dict | None] = mapped_column(JSONB)
    final_rank: Mapped[int | None] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(BigInteger)
    processed_revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    statistics: Mapped[dict | None] = mapped_column(JSONB)


class ActivityMonthlySeason(Base):
    __tablename__ = "activity_monthly_seasons"
    __table_args__ = (
        CheckConstraint("previous_season_id IS NULL OR previous_season_id <> season_id"),
    )
    season_id: Mapped[str] = mapped_column(
        ForeignKey("activity_seasons.id", ondelete="CASCADE"), primary_key=True
    )
    previous_season_id: Mapped[str | None] = mapped_column(
        ForeignKey("activity_seasons.id"), unique=True
    )


class ActivityHoldingPeriod(Base):
    __tablename__ = "activity_holding_periods"
    __table_args__ = (
        CheckConstraint("origin IN ('ACQUIRED','IMPORTED')"),
        CheckConstraint("ended_ms IS NULL OR ended_ms >= started_ms"),
        CheckConstraint("verified_from_ms IS NULL OR verified_from_ms >= started_ms"),
        CheckConstraint("(ended_ms IS NULL) = (end_order IS NULL)"),
        Index(
            "activity_one_open_holding",
            "season_id",
            "site_id",
            unique=True,
            postgresql_where=text("ended_ms IS NULL"),
        ),
        Index("activity_holding_pet", "season_id", "pet_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    season_id: Mapped[str] = mapped_column(ForeignKey("activity_seasons.id", ondelete="CASCADE"))
    pet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"))
    site_id: Mapped[str] = mapped_column(ForeignKey("territory_claim_sites.site_id"))
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("territory_claims.id", ondelete="SET NULL")
    )
    game_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("territory_claim_sessions.id", ondelete="SET NULL")
    )
    started_ms: Mapped[int] = mapped_column(BigInteger)
    ended_ms: Mapped[int | None] = mapped_column(BigInteger)
    verified_from_ms: Mapped[int | None] = mapped_column(BigInteger)
    start_order: Mapped[int] = mapped_column(BigInteger)
    end_order: Mapped[int | None] = mapped_column(BigInteger)
    origin: Mapped[str] = mapped_column(String)
    takeover: Mapped[bool] = mapped_column(Boolean)


class ActivityGameReceipt(Base):
    __tablename__ = "activity_game_receipts"
    __table_args__ = (CheckConstraint("bonus >= 0"),)
    season_id: Mapped[str] = mapped_column(
        ForeignKey("activity_seasons.id", ondelete="CASCADE"), primary_key=True
    )
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    pet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"))
    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("territory_claims.id", ondelete="CASCADE")
    )
    bonus: Mapped[int] = mapped_column(BigInteger)
    at_ms: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String)


class ActivityBonusKey(Base):
    __tablename__ = "activity_bonus_keys"
    season_id: Mapped[str] = mapped_column(
        ForeignKey("activity_seasons.id", ondelete="CASCADE"), primary_key=True
    )
    pet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pets.id", ondelete="CASCADE"), primary_key=True
    )
    site_id: Mapped[str] = mapped_column(
        ForeignKey("territory_claim_sites.site_id"), primary_key=True
    )
    utc_day: Mapped[int] = mapped_column(BigInteger, primary_key=True)
