"""초대 묶음에 담긴 강아지. 원본 스키마는 `db/init/24_pet_members.sql` 입니다.

초대 하나에 강아지 여러 마리를 담습니다 (MVP 결정 §2). **`pet_invites.pet_id` 는 앵커로
남습니다** — 승계의 옛 링크 청소·만료 청소·활성 수 세기와 구 앱의
`GET /app/pets/{pet_id}/invites` 가 그 칸을 보기 때문입니다.

`linked_pet_id` 는 **영수증의 일부**입니다. `pet_invites.accepted_by` 만으로는 "누가 언제
받았는지" 까지만 알 수 있어, 응답을 못 받은 재시도에 **강아지별 연결 결과**를 되살릴 수
없습니다 (`services/pet_member.py::accept_invite`).
"""

import uuid

from sqlalchemy import ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class PetInvitePet(Base):
    __tablename__ = "pet_invite_pets"

    invite_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pet_invites.id", ondelete="CASCADE"), primary_key=True
    )

    #: ⚠️ CASCADE 라 강아지가 지워지면 이 줄이 **조용히** 사라집니다. 그래서 묶음 구성이
    #: 바뀐 것을 `pet_invites.pet_count` 와 견줘서 알아냅니다 — 안 그러면 남은 강아지만
    #: 부분 수락됩니다 (MVP 결정 §2 "묶음 불변성").
    pet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="CASCADE"), primary_key=True
    )

    #: 수락 때 받는 사람의 어느 pet 행에 연결했는지. **NULL 이면 연결 없이 참여**입니다.
    #: SET NULL 인 이유는 `accepted_by` 와 같습니다 — 영수증은 행을 지우지 않고 사람만
    #: 비웁니다.
    linked_pet_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:
        return f"<PetInvitePet invite={self.invite_id} pet={self.pet_id}>"
