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
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr, model_validator

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


class VetVisitReasonOptionOut(BaseModel):
    """[edit] 드롭다운 한 줄 — 코드와 표시명을 같이 낸다 (`services.vet_visit.
    ReasonOption` 과 같은 모양). 표시명을 여기서 안 실으면 앱이 17개 한글 표시명을
    직접 하드코딩해야 하고, 그것이 닫힌 목록으로 막으려던 드리프트다 (docs §2)."""

    code: VetVisitReasonCode
    label: str


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
    #: [edit] 드롭다운 — 이 강아지가 실제로 겪은 사유가 맨 앞이다. 코드만이 아니라
    #: 표시명도 같이 온다 — 앱이 한글 라벨을 하드코딩하지 않게.
    reason_options: list[VetVisitReasonOptionOut]


#: 구 모양(평평한 본문)에서 `splits` 한 칸으로 접히는 필드들. 호환 껍데기가 지워질 때
#: 이 튜플과 `_fold_legacy_shape` 가 같이 지워진다.
_LEGACY_SPLIT_FIELDS = (
    "client_event_id",
    "reason_code",
    "reason_detail",
    "total_krw",
    "is_emergency",
    "is_oncology",
)


class VetVisitSplitIn(BaseModel):
    """확정될 행 하나 = 아이 하나 (docs §2 다견).

    **`client_event_id` 가 행마다 하나씩**인 이유는 `vet_visits` 의 UNIQUE 가
    `(app_user_id, client_event_id)` 라 행 단위이기 때문이다. `pet_id` 를 안 보내면
    초안의 강아지다 — 한 마리 확정과 구 모양 호환이 그 자리다.
    """

    client_event_id: uuid.UUID
    pet_id: uuid.UUID | None = None
    reason_code: VetVisitReasonCode
    reason_detail: str | None = Field(default=None, max_length=60)
    #: 이 아이 몫. 합이 영수증 총액과 다르면 422 다.
    total_krw: int = Field(ge=0, le=100_000_000)
    is_emergency: bool = False
    #: 영수증에 찍힌 글자가 아니라 임상 판단이라 **유저만** 켠다 (docs §1).
    is_oncology: bool = False
    #: 몇 번째 `동물명` 블록인가 — `raw_ocr_items` 를 자르는 데만 쓰고 저장하지 않는다.
    patient_index: int | None = Field(default=None, ge=0, le=19)


class VetVisitConfirmRequest(BaseModel):
    """확정 한 번. **라벨만이 아니라 병원 이름·주소·전화번호도 여기로 고친다**
    (docs "확인 화면에서 고칠 수 있는 것") — 제안값이 저장소로 새는 다른 경로는 없다.

    **`splits` 는 언제나 있고 길이가 1 이상이다.** 한 마리는 특수 케이스가 아니라
    `len(splits) == 1` 이다 — 그래야 소유권 검사도 금액 검산도 항목 자르기도 서비스
    안쪽에 한 벌만 존재하고, 대다수인 한 마리 경로가 그 한 벌을 매일 밟는다.

    `visited_on` · `hospital_*` · `total_krw` 는 **영수증 단위**다. `total_krw` 는
    영수증에 찍힌 총액이고 `splits` 의 합과 대조된다.

    ⚠️ **아래 `_fold_legacy_shape` 는 한시적이다.** 앱이 새 버전으로 깔리면 그 검증기와
    `_LEGACY_SPLIT_FIELDS`, 그리고 `_from_legacy_shape` 를 읽는 라우터의 응답 분기가
    **한꺼번에** 지워진다. 그때부터 요청도 응답도 언제나 리스트다.
    """

    visited_on: date
    total_krw: int = Field(ge=0, le=100_000_000)
    hospital_name: str | None = Field(default=None, max_length=60)
    hospital_address: str | None = Field(default=None, max_length=200)
    #: `tel:` 링크가 되는 칸이다 — 잘못 읽힌 숫자가 모르는 사람에게 전화를 걸기 전에
    #: 여기서 422 로 막는다 (DB CHECK 와 같은 모양).
    hospital_phone: str | None = Field(
        default=None, max_length=32, pattern=VET_VISIT_PHONE_PATTERN
    )
    splits: list[VetVisitSplitIn] = Field(min_length=1, max_length=20)

    #: 구 모양으로 들어왔나. **OpenAPI 에 안 뜬다** — 앱이 보내는 값이 아니라 응답
    #: 모양을 고르려고 경계가 자기에게 남기는 표시다.
    _from_legacy_shape: bool = PrivateAttr(default=False)

    @model_validator(mode="wrap")
    @classmethod
    def _fold_legacy_shape(cls, data: Any, handler: Any) -> "VetVisitConfirmRequest":
        """평평한 구 본문을 1개짜리 `splits` 로 접는다. **호환은 여기 한 곳뿐이다.**

        앱은 스토어를 거쳐 깔리므로 구버전이 한동안 남는다. 그 두 모양이 서비스
        안쪽까지 들어오면 소유권 검사·금액 검산·항목 자르기가 전부 두 벌이 되고,
        `db/init/25_vet_visits.sql` 이 경계한 "한쪽만 고치는 날"이 온다.

        `mode="wrap"` 인 이유는 **원본 입력과 만들어진 인스턴스를 둘 다 봐야** 하기
        때문이다 — 어느 모양으로 들어왔는지는 접고 나면 사라지는데, 라우터가 응답
        모양을 고를 때 그것이 필요하다.
        """
        legacy = (
            isinstance(data, dict) and "splits" not in data and "client_event_id" in data
        )
        if legacy:
            folded = {k: v for k, v in data.items() if k not in _LEGACY_SPLIT_FIELDS}
            folded["total_krw"] = data.get("total_krw")
            folded["splits"] = [{k: data[k] for k in _LEGACY_SPLIT_FIELDS if k in data}]
            data = folded
        model = handler(data)
        model._from_legacy_shape = legacy
        return model


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
    #: `start` 보다 오래된 기록 수. 0 이 아니면 앱이 "`start` 이후만 보입니다" 를
    #: 띄우고 날짜를 고르게 한다 — 창 밖의 기록이 사라진 것처럼 보이지 않게.
    older_count: int


__all__ = [
    "VET_VISIT_PHONE_PATTERN",
    "VetVisitConfirmRequest",
    "VetVisitContentType",
    "VetVisitDraftResponse",
    "VetVisitListResponse",
    "VetVisitReasonCode",
    "VetVisitReasonOptionOut",
    "VetVisitReceiptItemOut",
    "VetVisitResponse",
    "VetVisitSplitIn",
    "VetVisitStartRequest",
    "VetVisitTicketResponse",
]
