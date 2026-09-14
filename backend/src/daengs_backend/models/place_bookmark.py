"""SQL source: db/init/34_place_bookmarks.sql."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class PlaceBookmark(Base):
    __tablename__ = "place_bookmarks"
    __table_args__ = (
        CheckConstraint(
            "source IN ('kcisa', 'kto', 'public:mois:animal_hospital', 'public:mois:animal_pharmacy')",
            name="place_bookmarks_source_check",
        ),
        CheckConstraint("length(ref) > 0", name="place_bookmarks_ref_check"),
    )
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    ref: Mapped[str] = mapped_column(String(256), primary_key=True)
    name: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
