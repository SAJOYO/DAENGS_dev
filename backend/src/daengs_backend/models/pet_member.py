"""공동 돌봄의 돌보미. 원본 스키마는 `db/init/24_pet_members.sql` 입니다.

**대표는 여기 없습니다.** `pets.app_user_id` 가 대표이고 이 표는 돌보미만 담습니다 —
구성원은 둘의 합집합입니다. `role` 칸이 없는 이유가 그것입니다.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class PetMember(Base):
    __tablename__ = "pet_members"

    pet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="CASCADE"), primary_key=True
    )

    #: ⚠️ FK 의 CASCADE 는 **안 돕니다** — 탈퇴가 `app_users` 행을 안 지웁니다.
    #: 탈퇴한 돌보미를 지우는 것은 `pet_membership_owner_cleanup` 트리거입니다.
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="CASCADE"), primary_key=True
    )

    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<PetMember pet={self.pet_id} user={self.app_user_id}>"
