"""진료비 기록 API(`/app/vet-visits`)의 요청 / 응답 형태 (#353).

라우터가 그대로 응답 모양으로 쓴다. 판단은 없다 — `services/vet_visit.py` 가 이미
정한 것을 여기서는 옮겨 담기만 한다.

⚠️ **`items` 를 받는 필드가 여기 하나도 없다.** 확정(`VetVisitConfirmRequest`)의
`raw_ocr_items` 는 초안에서만 읽는다(docs/vet-visits.md §3) — 요청 본문에 항목을
실을 자리를 만들면, 그 자리 하나가 동의 분기를 우회하는 문이 된다. 이 파일에 다시
`items` 비슷한 필드를 더하고 싶어지면 그 판단부터 다시 보라는 뜻이다.
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from daengs_backend.models import VET_REASON_CODES

#: 영수증 사진으로 받는 형식. `screening.ScreeningContentType` 과 같은 목록이다.
VetVisitContentType = Literal["image/jpeg", "image/webp"]

#: 유저가 확정할 수 있는 사유. `VET_REASON_CODES` 를 그대로 풀어 쓴다 — 두 자리에서
#: 따로 적으면 언젠가 어긋나고, 어긋나면 여기는 통과하고 DB CHECK 에서 500 이 난다.
VetVisitReasonCode = Literal[*VET_REASON_CODES]

#: `hospital_phone` 의 모양. DB CHECK(`vet_visits_hospital_phone_check`)와 같은
#: 정규식이다 — 여기서 막아야 앱이 `tel:` 링크로 모르는 사람에게 전화를 거는 값을
#: 확인 화면을 지나 저장하지 않는다.
VET_VISIT_PHONE_PATTERN = r"^[0-9]{2,4}(-[0-9]{3,4}){1,2}$"


class VetVisitStartRequest(BaseModel):
    """초안 한 줄을 열고 사진 올릴 자리를 받는다. 멱등키(`client_event_id`)는 앱이
    만든다 — 탭 두 번·재시도가 두 줄이 되면 안 된다 (`care_events` 와 같은 규칙)."""

    pet_id: uuid.UUID
    content_type: VetVisitContentType = "image/jpeg"
    client_event_id: uuid.UUID


class VetVisitTicketResponse(BaseModel):
    """초안 하나와, 그 영수증 사진을 올릴 자리."""

    draft_id: uuid.UUID
    pet_id: uuid.UUID
    storage_key: str
    upload_url: str
    upload_headers: dict[str, str]
    expires_in_seconds: int


class VetVisitReceiptItemOut(BaseModel):
    """추출된 진료 항목 하나. `services.vet_receipt.ReceiptItem` 과 같은 모양이다."""

    name: str
    amount_krw: int


class VetVisitDraftResponse(BaseModel):
    """확인 화면 하나를 채우는 값 전부 (docs §2 "초안 응답").

    `extraction_status` 가 `unreadable`·`failed` 여도 나머지 칸은 그냥 비어 있을
    뿐이다 — 유저는 이 화면에서 손으로 채워 그대로 확정할 수 있다.
    """

    draft_id: uuid.UUID
    pet_id: uuid.UUID
    #: 올라온 영수증 사진 주소. 초안은 사진 없이는 존재하지 않아 항상 있다.
    receipt_image_url: str | None
    extraction_status: Literal["ok", "unreadable", "failed"]
    unreadable_reason: Literal["blurry", "not_a_receipt", "no_amount"] | None
    visited_on: date | None
    total_krw: int | None
    #: 확인 화면에서 고칠 수 있는 병원 정보 — OCR 이 뒤집을 수 있는 자리다.
    hospital_name: str | None
    hospital_address: str | None
    hospital_phone: str | None
    items: list[VetVisitReceiptItemOut]
    #: 목록 안의 값이거나 `null`(제안 없음). `null` 이면 확인 화면이 아무것도
    #: 미리 고르지 않는다.
    suggested_reason_code: VetVisitReasonCode | None
    #: OCR 이 채우고 유저가 고친다 — 야간·응급·공휴일 할증이 항목으로 찍혔을 때만.
    is_emergency: bool
    #: 같은 `(pet_id, visited_on, total_krw)` 로 **확정된** 기록이 이미 있다.
    possible_duplicate: bool
    #: [edit] 드롭다운 — 이 강아지가 실제로 겪은 사유가 맨 앞이다.
    reason_options: list[str]


class VetVisitConfirmRequest(BaseModel):
    """확정 한 번. **라벨만이 아니라 병원 이름·주소·전화번호도 여기로 고친다**
    (docs "확인 화면에서 고칠 수 있는 것") — 제안값이 저장소로 새는 다른 경로는 없다.
    """

    client_event_id: uuid.UUID
    reason_code: VetVisitReasonCode
    reason_detail: str | None = Field(default=None, max_length=60)
    visited_on: date
    total_krw: int = Field(ge=0, le=100_000_000)
    hospital_name: str | None = Field(default=None, max_length=60)
    hospital_address: str | None = Field(default=None, max_length=200)
    #: `tel:` 링크가 되는 칸이다 — 잘못 읽힌 숫자가 모르는 사람에게 전화를 걸기 전에
    #: 여기서 422 로 막는다 (DB CHECK 와 같은 모양).
    hospital_phone: str | None = Field(
        default=None, max_length=32, pattern=VET_VISIT_PHONE_PATTERN
    )
    is_emergency: bool = False
    #: 영수증에 찍힌 글자가 아니라 임상 판단이라 **유저만** 켠다 (docs §1).
    is_oncology: bool = False


class VetVisitResponse(BaseModel):
    """확정된 기록 하나. `raw_ocr_items` 는 안 싣는다 — 학습용 원본이고 확인 화면이
    이미 보여 준 값을 되돌려 줄 이유가 없다 (채팅 맥락이 그것을 뺀 것과 같은 결)."""

    id: uuid.UUID
    pet_id: uuid.UUID
    visited_on: date
    total_krw: int
    hospital_name: str | None
    hospital_address: str | None
    hospital_phone: str | None
    reason_code: str
    reason_detail: str | None
    suggested_reason_code: str | None
    is_emergency: bool
    is_oncology: bool
    client_event_id: uuid.UUID
    created_at: datetime


class VetVisitListResponse(BaseModel):
    pet_id: uuid.UUID
    #: 실제로 조회한 창. 앱이 안 보내면 서비스 기본값(최근 1년)이 여기 적혀 온다.
    start: date
    end: date
    visits: list[VetVisitResponse]


__all__ = [
    "VET_VISIT_PHONE_PATTERN",
    "VetVisitConfirmRequest",
    "VetVisitContentType",
    "VetVisitDraftResponse",
    "VetVisitListResponse",
    "VetVisitReasonCode",
    "VetVisitReceiptItemOut",
    "VetVisitResponse",
    "VetVisitStartRequest",
    "VetVisitTicketResponse",
]
