"""공동 돌봄 초대. 원본 스키마는 `db/init/24_pet_members.sql` 입니다.

**평문 토큰이 없습니다** — `token_hash` 만 둡니다 (`refresh_tokens` 와 같은 규칙).

`accepted_at` · `accepted_by` 는 2026-09-10 에 더해졌습니다(#388, #261). **수락은 더 이상
행을 지우지 않습니다** — 지우면 응답을 못 받은 재시도가 404 를 받는데, 이미 다른 아이를
돌보는 사람에게는 그것이 "실패"인지 "이미 성공"인지 구별할 수 없었습니다. 이제 이 두 칸이
영수증입니다: 같은 사람이 같은 토큰으로 다시 오면 그대로 200, 다른 사람이 오면 여전히
404 입니다 (`services/pet_member.py::accept_invite`). 영수증의 수명은 `expires_at` 그대로라
`delete_expired_invites` 가 만료건을 치울 때 수락 여부를 가리지 않습니다.
"""

import uuid
from datetime import datetime

from sqlalchemy import CHAR, DateTime, ForeignKey, SmallInteger, Uuid, text
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

    #: 묶음에 **원래** 몇 마리가 있었나 (다중 초대 MVP, MVP 결정 §2).
    #:
    #: `pet_invite_pets.pet_id` 가 CASCADE 라 강아지가 지워지면 자식 줄이 조용히
    #: 사라집니다. 그러면 남은 강아지만으로 **부분 수락**이 되는데, 그것이 바로 제품이
    #: 금지한 동작입니다. 수락할 때 자식 수와 이 값을 견줘 다르면 묶음 전체를 410 으로
    #: 무효화합니다 — "하나라도 무효면 전부" 가 원자성 규칙과 같은 결입니다.
    #:
    #: 기본값 1 은 **배포 창** 때문입니다 — 마이그레이션이 서버보다 먼저 나가므로
    #: (MVP 결정 §9) 그 사이 옛 코드가 이 칸 없이 INSERT 합니다. 옛 코드는 한 마리
    #: 초대만 만드니 1 이 정확합니다.
    pet_count: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    #: 수락 시각. NULL 이면 아직 안 쓴 초대입니다.
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    #: 수락한 사람. **이 값만 다른 사람과 재시도를 가릅니다** — 같으면 200, 다르면 404.
    #: SET NULL 인 이유는 care_events.actor_app_user_id 와 같습니다: 영수증(그날 이 사람이
    #: 받았다는 사실)은 그 사람이 훗날 진짜로 지워지는 날에도 남아야 합니다. 다만 탈퇴는
    #: app_users 행을 안 지우므로(§1 "함정") 이 SET NULL 은 실질적으로 거의 안 돕니다.
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:
        # 토큰 해시는 repr 로 흘리지 않습니다.
        return f"<PetInvite {self.id} pet={self.pet_id}>"
