from datetime import datetime
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Place(Base):
    __tablename__ = "place"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String)          # hospital | pharmacy
    name: Mapped[str] = mapped_column(String)
    address: Mapped[str | None] = mapped_column(String)
    phone: Mapped[str | None] = mapped_column(String)
    location = mapped_column(Geography(geometry_type="POINT", srid=4326), nullable=False)
    is_night: Mapped[bool] = mapped_column(Boolean, default=False)
    is_24h: Mapped[bool] = mapped_column(Boolean, default=False)
    hours: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # DB 컬럼은 TEXT[]. ARRAY(String)이면 바인드가 VARCHAR[]로 나가서
    # tags && / @> 연산자가 통째로 깨진다 (text[] && varchar[] 연산자가 없다).
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    area_m2: Mapped[float | None] = mapped_column(Numeric)
    staff_count: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String)
    source_id: Mapped[str | None] = mapped_column(String)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    license_status_code: Mapped[str | None] = mapped_column(String)
    license_status_name: Mapped[str | None] = mapped_column(String)
    coordinate_source: Mapped[str | None] = mapped_column(String)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
