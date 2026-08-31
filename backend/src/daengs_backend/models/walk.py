"""앱 회원의 산책 기록. 원본 스키마는 `db/init/06_walks.sql` 입니다.

**끝난 산책만 들어옵니다.** 기기가 강제 종료되어 열린 채 남은 세션은 기록이 아니라
사고의 흔적이라 앱이 올리지 않습니다 — 그래서 `ended_at` 이 NOT NULL 입니다.

거리와 시간은 **컬럼이 없습니다.** 좌표에서 다시 계산하는 값이라 저장하면 계산 규칙을
고쳤을 때 저장된 숫자와 새로 계산한 숫자가 갈라집니다. 앱의 `WalkSummary` 도 같은
이유로 요약을 저장하지 않습니다.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from daengs_backend.models.base import Base


class Walk(Base):
    __tablename__ = "walks"

    __table_args__ = (
        CheckConstraint("ended_at >= started_at", name="walks_time_order"),
        # **재시도가 안전해야 합니다.** 앱은 네트워크가 끊기면 다음에 다시 올리는데,
        # 그때 같은 산책이 두 건이 되면 안 됩니다.
        UniqueConstraint(
            "app_user_id", "client_session_id", name="walks_client_session_unique"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    # 계정이 지워지면 산책도 같이 지웁니다. 산책 경로는 집과 생활권을 그대로
    # 드러내므로, 탈퇴가 개인정보를 파기하는데 이게 남으면 파기가 반쪽입니다.
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="CASCADE")
    )

    # **강아지를 지워도 산책은 남습니다.** 무지개다리를 건넌 아이와의 산책이 그 아이를
    # 지웠다고 없던 일이 되면 안 됩니다. 기록은 사람의 것입니다.
    pet_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="SET NULL")
    )

    # 기기의 로컬 DB 와 **같은 값**입니다. 올릴 때도 되찾을 때도 이 id 로 맞춰 봅니다.
    client_session_id: Mapped[uuid.UUID] = mapped_column(Uuid)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # 나갈 때의 날씨. WMO 원본 코드입니다. 못 받았으면 셋 다 None 이고,
    # **그걸 "맑음"으로 채우지 않습니다.**
    weather_code: Mapped[int | None] = mapped_column(Integer)
    is_day: Mapped[bool | None] = mapped_column(Boolean)
    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    points: Mapped[list["WalkPoint"]] = relationship(
        back_populates="walk",
        cascade="all, delete-orphan",
        order_by="WalkPoint.client_seq",
    )

    def __repr__(self) -> str:
        return f"<Walk {self.id} started={self.started_at}>"


class WalkPoint(Base):
    """기기가 준 **원본 좌표**. 화면용으로 거르기 전의 값입니다.

    흔들림을 걸러내는 문턱값은 나중에 바뀔 수 있고, 그때 버린 점을 되살릴 수 있어야
    합니다 — 기기의 로컬 DB 가 원본만 남기는 것과 같은 이유입니다.
    """

    __tablename__ = "walk_points"

    walk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("walks.id", ondelete="CASCADE"), primary_key=True
    )

    # PK 의 일부입니다 — **같은 점을 두 번 보내도 한 줄**입니다.
    client_seq: Mapped[int] = mapped_column(Integer, primary_key=True)

    # 일시정지나 GPS 점프 뒤에 증가합니다. 값이 다른 두 점을 직선으로 이으면 걷지
    # 않은 길이 그려지므로 서버도 그대로 보관합니다.
    chain_index: Mapped[int] = mapped_column(Integer)

    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lat: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    lng: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    accuracy_m: Mapped[float | None] = mapped_column(Float)

    # 가상 위치로 만든 기록. 지우지 않고 표시만 해 둡니다 — 나중에 점수나 랭킹이
    # 생기면 걸러야 할 값이고, 그때 원본이 없으면 가릴 수가 없습니다.
    is_mock: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))

    walk: Mapped[Walk] = relationship(back_populates="points")
