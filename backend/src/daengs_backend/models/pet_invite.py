"""공동 돌봄 초대. 원본 스키마는 `db/init/24_pet_members.sql` 입니다.

**평문 토큰이 없습니다** — `token_hash` 만 둡니다 (`refresh_tokens` 와 같은 규칙).
`accepted_at` 도 없습니다: 수락하면 행을 지워서 일회용이 됩니다.
"""

import uuid
from datetime import datetime

from sqlalchemy import CHAR, DateTime, ForeignKey, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class PetInvite(Base):
    __tablename__ = "pet_invites"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    pet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="CASCADE")
    )

    #: 초대한 대표. 대표가 바뀌면 이전 대표가 뿌린 초대를 무효로 보는 데 씁니다.
    invited_by: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="CASCADE")
    )

    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        # 토큰 해시는 repr 로 흘리지 않습니다.
        return f"<PetInvite {self.id} pet={self.pet_id}>"
