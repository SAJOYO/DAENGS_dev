"""Member-owned saved sites. Authoritative SQL: 33_territory_bookmarks.sql."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class TerritoryBookmark(Base):
    __tablename__ = "territory_bookmarks"
    __table_args__ = (
        CheckConstraint(
            "site_id ~ '^territory-site:hex-v1:140:-?[0-9]+:-?[0-9]+$'",
            name="territory_bookmarks_site_id_check",
        ),
        Index("territory_bookmarks_site_idx", "site_id", "app_user_id"),
    )
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), primary_key=True
    )
    # Place owns the complete board; claim_sites contains only previously touched sites.
    site_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
