"""Member entitlement and per-event breakdown; SQL: db/init/29_activity_rewards.sql."""

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class ActivityBaseReward(Base):
    __tablename__ = "activity_base_rewards"
    __table_args__ = (
        CheckConstraint("paid IN (0,20,100)"),
        Index("activity_base_rewards_member", "app_user_id"),
    )
    season_id: Mapped[str] = mapped_column(
        ForeignKey("activity_seasons.id", ondelete="CASCADE"), primary_key=True
    )
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), primary_key=True
    )
    site_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("territory_claim_sites.site_id"), primary_key=True
    )
    paid: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class ActivityRewardDetail(Base):
    __tablename__ = "activity_reward_details"
    __table_args__ = (
        ForeignKeyConstraint(
            ["season_id", "event_id"],
            ["activity_game_receipts.season_id", "activity_game_receipts.event_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["season_id", "app_user_id", "site_id"],
            [
                "activity_base_rewards.season_id",
                "activity_base_rewards.app_user_id",
                "activity_base_rewards.site_id",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint("reward_version='first-season-rewards-v1'"),
        CheckConstraint("base_before IN (0,20,100)"),
        CheckConstraint("base_after IN (0,20,100)"),
        CheckConstraint("base_points >= 0"),
        CheckConstraint("takeover_points IN (0,20)"),
        CheckConstraint("base_after=base_before+base_points"),
    )
    season_id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    app_user_id: Mapped[uuid.UUID]
    site_id: Mapped[str] = mapped_column(String(96))
    reward_version: Mapped[str] = mapped_column(String)
    base_before: Mapped[int] = mapped_column(BigInteger)
    base_after: Mapped[int] = mapped_column(BigInteger)
    base_points: Mapped[int] = mapped_column(BigInteger)
    takeover_points: Mapped[int] = mapped_column(BigInteger)
