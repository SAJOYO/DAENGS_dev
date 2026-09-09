"""영수증에서 읽은 진료비 기록과 확정 전 초안. 원본 스키마는 `db/init/25_vet_visits.sql`.

**표가 둘인 것이 설계다** — 기계가 추측한 라벨은 `VetVisitDraft` 에만 있고, 유저가
확정한 것만 `VetVisit` 로 넘어온다. 채팅도 목록도 `VetVisit` 만 읽으므로 확정 안 된
추측은 거르는 게 아니라 거기 없다 (docs/vet-visits.md §1).
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base

#: 방문이 겨눈 것. **축이 하나다** — 신체계통 12 · 예방 4 · 기타 1.
#: 병리(tumor·injury·parasite)와 응급도(emergency)는 여기 없다: 방문이 겨눈 대상이
#: 아니라 방문의 성질이라, 코드로 두면 한 방문에 코드가 둘씩 맞아떨어진다.
#: 그 둘은 `is_oncology` · `is_emergency` 칸이 받는다.
VET_REASON_CODES = (
    "skin",
    "ear",
    "eye",
    "dental",
    "digestive",
    "respiratory",
    "cardiac",
    "urinary",
    "reproductive",
    "musculoskeletal",
    "neurologic",
    "endocrine",
    "vaccination",
    "parasite_prevention",
    "checkup",
    "neuter",
    "other",
)

#: 표시명. DB 에는 코드만 앉는다 (`CARE_EVENT_KINDS` 와 같은 규칙).
VET_REASON_LABELS = {
    "skin": "피부",
    "ear": "귀",
    "eye": "안과",
    "dental": "구강·치과",
    "digestive": "소화기",
    "respiratory": "호흡기",
    "cardiac": "심장",
    "urinary": "비뇨기",
    "reproductive": "생식기",
    "musculoskeletal": "근골격",
    "neurologic": "신경",
    "endocrine": "내분비",
    "vaccination": "예방접종",
    "parasite_prevention": "구충·예방",
    "checkup": "건강검진",
    "neuter": "중성화",
    "other": "기타",
}

_CODES_SQL = ",".join(f"'{c}'" for c in VET_REASON_CODES)


class VetVisit(Base):
    __tablename__ = "vet_visits"
    __table_args__ = (
        CheckConstraint(
            f"reason_code IN ({_CODES_SQL})", name="vet_visits_reason_code_check"
        ),
        UniqueConstraint(
            "app_user_id", "client_event_id", name="vet_visits_client_event_unique"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="CASCADE")
    )
    pet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="CASCADE")
    )

    #: 영수증에 찍힌 **날짜**다. 시각은 안 남긴다 — 진료비에 시분은 아무 질문도 안 가른다.
    visited_on: Mapped[date] = mapped_column(Date)

    #: 원(KRW) 정수. 상한 1억은 OCR 이 자릿수를 흘리는 실패의 터무니없는 쪽만 걸러낸다.
    total_krw: Mapped[int] = mapped_column(Integer)

    hospital_name: Mapped[str | None] = mapped_column(String(60))
    hospital_address: Mapped[str | None] = mapped_column(String(200))
    #: `tel:` 링크가 되므로 숫자·하이픈 모양은 SQL CHECK 가 지킨다.
    hospital_phone: Mapped[str | None] = mapped_column(String(32))

    #: 유저가 확정한 사유. 닫힌 목록이고 축이 하나다 (모듈 머리말).
    reason_code: Mapped[str] = mapped_column(String(20))
    #: 유저가 덧붙인 한 줄. 집계에도 프롬프트에도 안 간다.
    reason_detail: Mapped[str | None] = mapped_column(String(60))
    #: 기계가 뭐라고 제안했는지. NULL 이면 제안 없음, 같으면 수용, 다르면 유저가 고침
    #: — 그래서 label_source 칸을 따로 두지 않는다.
    suggested_reason_code: Mapped[str | None] = mapped_column(String(20))

    #: 응급 방문이었나. 코드가 아니라 칸인 이유는 방문의 성질이라서다.
    is_emergency: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    #: 종양 진료였나. 기계가 못 채운다 — 확인 화면의 체크 하나로 유저만 켠다.
    is_oncology: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    #: 추출된 진료 항목 [{"name": ..., "amount_krw": ...}, ...].
    #: 보호자 이름·전화·카드번호는 여기 없다 — 추출 스키마에 그 칸 자체가 없다.
    raw_ocr_items: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )

    #: 영수증 사진. 손입력이면 NULL 이다.
    receipt_image_key: Mapped[str | None] = mapped_column(Text)

    #: 멱등키. `care_events` 는 pet 단위지만 여기는 **유저 단위**로 묶는다.
    client_event_id: Mapped[uuid.UUID] = mapped_column(Uuid)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<VetVisit {self.id} pet={self.pet_id} {self.reason_code} {self.total_krw}>"


class VetVisitDraft(Base):
    __tablename__ = "vet_visit_drafts"
    __table_args__ = (
        UniqueConstraint(
            "app_user_id", "client_event_id", name="vet_visit_drafts_client_event_unique"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="CASCADE")
    )
    pet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="CASCADE")
    )

    receipt_image_key: Mapped[str] = mapped_column(Text)

    #: 추출 결과 통째로. 확정 전 값이라 칼럼으로 안 쪼갠다 (docs §1).
    #: **미동의면 여기에 items 가 없다** (docs §3).
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: 멱등키. 확정은 사람이 한 번 누르는 버튼이지만, 업로드는 두 번 눌리는 버튼이다.
    client_event_id: Mapped[uuid.UUID] = mapped_column(Uuid)

    #: 올라온 사진의 sha256. client_event_id 가 못 잡는 경우(앱 재시작)를 잡는다.
    receipt_sha256: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<VetVisitDraft {self.id} pet={self.pet_id} extracted={self.extracted_at}>"
