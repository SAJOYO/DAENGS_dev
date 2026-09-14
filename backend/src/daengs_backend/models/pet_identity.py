"""논리 강아지. 원본 스키마는 `db/init/25_pet_identities.sql` 입니다.

여러 `pets` 행이 **같은 실제 강아지**임을 나타냅니다. 물리 병합이 아닙니다 — 기존 행도
기록도 옮기거나 지우지 않고 관계만 더합니다 (docs/co-care.md).

**그룹의 주보호자를 사람이 아니라 `owner_pet_id`(대표 pet 행)로 가리킵니다.**
`pet_members` 에 `role` 칸을 안 둔 것과 같은 이유입니다 — 주보호자의 원본은
`pets.app_user_id` 하나뿐이라, 여기에 사람 id 를 또 두면 승계 때 두 곳이 어긋납니다.
그룹 주보호자는 언제나 `pets[owner_pet_id].app_user_id` 로 유도합니다.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class PetIdentity(Base):
    __tablename__ = "pet_identities"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    #: 그룹의 앵커. 이 행의 `app_user_id` 가 곧 그룹 주보호자입니다.
    #:
    #: CASCADE 인 이유 — 앵커 pet 행이 지워지면 그룹 자체가 뜻을 잃습니다. 그때 이 행이
    #: 사라지고 `pets.identity_id` 의 SET NULL 이 남은 행들을 독립 강아지로 되돌립니다.
    #: **남은 사람들의 pet 행과 기록은 그대로 남습니다.**
    owner_pet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="CASCADE"), unique=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<PetIdentity {self.id} owner_pet={self.owner_pet_id}>"
