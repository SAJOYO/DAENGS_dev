"""강아지별 케어 이벤트 — 밥 · 약 · 간식. 원본 스키마는 `db/init/23_care_events.sql` 입니다.

**산책은 여기 없습니다.** `walks` 가 이미 진실이라 `kind` 에 `walk` 를 두지 않습니다 — 한 사실이
두 곳에 있으면 반드시 어긋납니다. 하루 요약이 `walks` 를 세어 같이 보여 줄 뿐입니다.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base

#: 무엇을 챙겼나. DB CHECK 와 같은 목록입니다. **`walk` 가 없는 것이 결정입니다** (모듈 머리말).
CARE_EVENT_KINDS = ("meal", "medication", "snack")


class CareEvent(Base):
    __tablename__ = "care_events"

    __table_args__ = (
        CheckConstraint(
            "kind IN ('meal','medication','snack')", name="care_events_kind_check"
        ),
        CheckConstraint(
            "note IS NULL OR length(btrim(note)) > 0", name="care_events_note_not_blank"
        ),
        # 멱등키. 앱이 만든 id 라 강아지 안에서만 유일하면 됩니다 (SQL 주석).
        UniqueConstraint("pet_id", "client_event_id", name="care_events_client_event_unique"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    #: **챙긴 사람.** 소유자가 아닙니다 — 이 기록의 주인은 강아지입니다 (docs/co-care.md).
    #:
    #: `None` 은 **탈퇴한 보호자**입니다. 화면에 이름을 낼지는 "지금도 구성원인가" 가 정하고,
    #: 그 규칙은 `services/pet_member.py` 의 `actor_label` 하나입니다.
    actor_app_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="SET NULL")
    )
    #: 강아지를 지우면 기록도 같이 지워집니다. 배웅은 행을 안 지우므로 배웅한 아이의 기록은 남습니다.
    pet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="CASCADE")
    )

    kind: Mapped[str] = mapped_column(String(12))

    #: 챙긴 시각. **앱이 보낸 시각**이지 서버가 받은 시각이 아닙니다 — 받은 시각은 `created_at`.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    #: 짧은 메모. 비었으면 None 입니다 — 빈 문자열을 넣지 않습니다.
    note: Mapped[str | None] = mapped_column(String(120))

    client_event_id: Mapped[uuid.UUID] = mapped_column(Uuid)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<CareEvent {self.id} pet={self.pet_id} {self.kind}@{self.occurred_at}>"
