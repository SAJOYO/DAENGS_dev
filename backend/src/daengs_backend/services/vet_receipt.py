"""영수증 사진 → 구조화된 값. **DB 를 모릅니다** (#353).

`adapters/general.py` 와 같은 `google-genai` 경로에 **제약 디코딩**(`response_json_schema`)
입니다. 새 벤더도 새 키도 없습니다.

**개인정보는 칸이 없어서 안 나옵니다.** 이 스키마에 보호자 이름·전화·카드번호·
사업자등록번호를 담을 필드가 하나도 없고, 제약 디코딩이라 모델이 스키마 밖의 키를 못
만듭니다. 값 안에 숨어 들어오는 경우 둘에만 그물이 따로 있습니다 —
`hospital_phone` 의 패턴과 `scrub_items` 입니다 (docs/vet-visits.md §2).

**모델 ID 는 여기 상수입니다, `.env` 가 아닙니다** — 라우터 모델과 같은 규칙입니다
(config.py "라우터 모델은 계약상 고정이라 .env 로 안 뺍니다"). `VetReasonCode` 는
`VET_REASON_CODES` 를 그대로 풀어 적습니다(`Literal[*VET_REASON_CODES]` 가 아니라) —
이 타입이 Pydantic 의 JSON 스키마 생성을 거쳐 제약 디코딩으로 들어가서, 명시적 리터럴이
생성된 스키마를 읽기 쉽고 안정적으로 만듭니다.
"""

import asyncio
import json
import logging
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from daengs_backend.models import VET_REASON_CODES
from daengs_backend.orchestration.semantic import (
    ROUTER_CANDIDATE_COUNT,
    ROUTER_TEMPERATURE,
    _gemini_client,
)

log = logging.getLogger(__name__)

VET_RECEIPT_MODEL_ID = "gemini-3.1-flash-lite"
VET_RECEIPT_PROMPT_VERSION = "vet-receipt-extract-v1"
#: 한국 전화번호 모양. **네 묶음이 안 맞는 것이 요점** — 카드번호가 이 칸에 못 앉습니다.
PHONE_PATTERN = r"^[0-9]{2,4}(-[0-9]{3,4}){1,2}$"
#: 답 문장 몇 개 + JSON 봉투. 항목이 최대 40개까지 실릴 수 있어 general 보다 넉넉히 둔다.
VET_RECEIPT_MAX_OUTPUT_TOKENS = 1024

#: 이 타입이 그대로 Pydantic JSON 스키마로 나가 제약 디코딩의 재료가 된다 —
#: `Literal[*VET_REASON_CODES]` 대신 풀어 적는 것이 규칙(컨트롤러 결정 #1).
VetReasonCode = Literal[
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
]

_REDACTED = "<redacted>"
_PII_PATTERNS = (
    re.compile(r"\d{8,}"),  # 연속 숫자 8자리 이상
    re.compile(r"\d{3}-\d{2}-\d{5}"),  # 사업자등록번호
    re.compile(r"\d{4}[- ]\d{4}[- ]\d{4}"),  # 카드
    re.compile(r"\d{2,3}-\d{3,4}-\d{4}"),  # 휴대폰
)


class ReceiptExtractionFailed(Exception):
    """우리 쪽 문제(타임아웃·API 오류). 라우터가 `failed` 로 옮깁니다 — 500 이 아닙니다."""


class ReceiptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=40)
    amount_krw: int = Field(ge=0, le=100_000_000)


class ReceiptExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "unreadable"]
    unreadable_reason: Literal["blurry", "not_a_receipt", "no_amount"] | None = None
    visited_on: date | None = None
    total_krw: int | None = Field(default=None, ge=0, le=100_000_000)
    hospital_name: str | None = Field(default=None, max_length=60)
    hospital_address: str | None = Field(default=None, max_length=200)
    hospital_phone: str | None = Field(default=None, pattern=PHONE_PATTERN)
    items: list[ReceiptItem] = Field(default_factory=list, max_length=40)
    suggested_reason_code: VetReasonCode | None = None
    is_emergency: bool = False

    @model_validator(mode="after")
    def shape_matches_status(self) -> "ReceiptExtraction":
        if self.status == "ok":
            if self.total_krw is None:
                raise ValueError("an ok extraction needs a total")
            return self
        if self.unreadable_reason is None:
            raise ValueError("an unreadable extraction needs a reason")
        if (
            self.total_krw is not None
            or self.items
            or self.hospital_name
            or self.hospital_address
            or self.hospital_phone
            or self.visited_on is not None
            or self.suggested_reason_code is not None
            or self.is_emergency
        ):
            raise ValueError("an unreadable extraction carries nothing else")
        return self


def scrub_items(items: list[ReceiptItem]) -> list[ReceiptItem]:
    """항목명에 숨어 들어온 개인정보를 지운다. **금액은 남긴다** — 항목을 통째로
    버리면 합계가 안 맞아 확인 화면이 거짓말을 하게 된다."""
    out = []
    for item in items:
        dirty = any(p.search(item.name) for p in _PII_PATTERNS)
        out.append(ReceiptItem(name=_REDACTED, amount_krw=item.amount_krw) if dirty else item)
    return out


# 프롬프트 규칙(docs/vet-visits.md §2 "프롬프트 규칙")을 영문 한 문단으로 옮긴 것이다 —
# `_CARE_LOG_RULE`(#344)과 같은 결. 애매한 자리 넷의 판정 규칙까지 여기 담는다.
_RECEIPT_RULE = """You read a Korean veterinary clinic receipt photo. Only transcribe what is actually printed on the receipt — never invent a value that is not there. total_krw comes only from a printed total line on the receipt — 합계, 총액, 청구금액, 받을금액, or the like — and never from anywhere else. Never compute total_krw: do not add up the line items, and do not derive it by any other arithmetic. A sum you calculated is not a total you read, even when it happens to match one. If no such printed total line is visible on the receipt — including when the photo is cropped or cut off before reaching it — output status="unreadable" with unreadable_reason="no_amount", even when every individual item's amount_krw was legible. Never write the payer's name, a personal phone number, a card number, or a business registration number anywhere in the output, including inside item names — if such a string appears next to an item, drop that part and keep only the item description. Amounts are integers in Korean won; strip commas and the "원" suffix, digits only. When a receipt breaks the amount into quantity / discount / amount columns plus a discount-total line, total_krw and every item's amount_krw are the post-discount amount column — never the discount column; the discount itself has no field and must not be captured anywhere. When the printed date is a range such as "2019-05-17 ~ 2019-05-17", visited_on is the start date of the range, even for a hospitalization stay where the two ends differ. hospital_phone is the clinic's own front-desk number, not the payer's — digits and hyphens only. suggested_reason_code must be one of the listed codes or null: when the items alone do not clearly point to one reason, output null (this leaves the confirmation screen with nothing pre-selected, which is the point) — reserve "other" for when the reason is clear but not in the list. Preventive care wins over the body system it happens to target: when an item is a vaccination or a preventive medication, use the matching preventive code even if the item also targets a body system — a ringworm vaccine that treats the skin is still "vaccination", not "skin", because owners ask "how much on prevention" and "how much because it was sick" as two separate questions. When a preventive act has no matching preventive code, fall back to the body-system code instead — a routine dental scaling is preventive but fits none of "vaccination" / "parasite_prevention" / "checkup" / "neuter", so it becomes "dental". Set is_emergency to true only when a line item such as a night-treatment fee, an emergency-treatment fee, or a holiday surcharge is actually printed — never because the treatment merely sounds urgent; this field records printed text, not judgment. When a checkup produced a targeted treatment or test on the same receipt, the body-system code wins over "checkup" — "checkup" is only for a checkup that is not followed by anything. When one visit lists several problems for an older dog, pick the single body system with the largest cost. At the mouth boundary, teeth and gums are "dental" while anything at or below the esophagus is "digestive". At the ear boundary, the outer ear and ear canal are "ear" while the inner ear and vestibular system are "neurologic". Heartworm prevention medication is "parasite_prevention" while treatment of an active heartworm infection is "cardiac" — an instance of the preventive-wins rule above. Neuter surgery and pyometra treatment open the same organs and look alike on a receipt — the diagnostic and procedure line items decide which. If the image is not a receipt, or blurry, output status="unreadable" with the matching reason instead."""


def build_receipt_prompt() -> str:
    schema = json.dumps(ReceiptExtraction.model_json_schema(), ensure_ascii=False, sort_keys=True)
    reason_codes = ", ".join(VET_REASON_CODES)
    return (
        f"PROMPT_VERSION: {VET_RECEIPT_PROMPT_VERSION}\n\n"
        f"{_RECEIPT_RULE}\n\n"
        f"suggested_reason_code must be one of: {reason_codes}, or null.\n\n"
        f"RECEIPT_EXTRACTION_JSON_SCHEMA:\n{schema}\n"
    )


def receipt_generation_config() -> Any:
    """Lazy for the same reason as ``semantic.router_generation_config``."""
    from google.genai import types

    return types.GenerateContentConfig(
        temperature=ROUTER_TEMPERATURE,
        candidate_count=ROUTER_CANDIDATE_COUNT,
        max_output_tokens=VET_RECEIPT_MAX_OUTPUT_TOKENS,
        response_mime_type="application/json",
        response_json_schema=ReceiptExtraction.model_json_schema(),
    )


async def _generate_with_gemini(image_bytes: bytes, content_type: str, prompt: str) -> object:
    def _call() -> object:
        from google.genai import types

        contents = [
            types.Part.from_bytes(data=image_bytes, mime_type=content_type),
            prompt,
        ]
        response = _gemini_client().models.generate_content(
            model=VET_RECEIPT_MODEL_ID,
            contents=contents,
            config=receipt_generation_config(),
        )
        parsed = getattr(response, "parsed", None)
        return parsed if parsed is not None else getattr(response, "text", None)

    return await asyncio.to_thread(_call)


def _validate_extraction(raw: object) -> ReceiptExtraction | None:
    parsed: object = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(parsed, ReceiptExtraction):
        return parsed
    try:
        return ReceiptExtraction.model_validate(parsed)
    except Exception:  # noqa: BLE001 - invalid model output is never surfaced
        return None


#: 전송 오류(타임아웃·API 오류)의 시도 횟수 — **한 번만 재시도한다** (docs §2 "못 읽었을 때").
#: 모델이 정상적으로 답한 `status="unreadable"` 은 실패가 아니라 이 재시도의 대상이 아니다.
VET_RECEIPT_TRANSPORT_ATTEMPTS = 2


async def extract(image_bytes: bytes, content_type: str) -> ReceiptExtraction:
    """영수증 사진 → `ReceiptExtraction`. **DB 를 모른다** (#353).

    전송·타임아웃 등 우리 쪽 문제는 `ReceiptExtractionFailed` 로 오른다 — 라우터가
    `status="failed"` 로 옮긴다. 모델이 "이 사진은 읽을 수 없다" 고 답한 것은 다르다
    — 정상적으로 반환된 `status="unreadable"` 이다.

    **전송 오류는 한 번만 재시도한다** (docs §2) — 총 두 번 부른다. 모델이 정상적으로
    돌려준 값을 파싱한 뒤(스키마가 안 맞아도)는 재시도하지 않는다 — 그건 모델의 답이지
    전송 실패가 아니다.
    """
    prompt = build_receipt_prompt()
    last_exc: Exception | None = None
    raw: object = None
    for attempt in range(VET_RECEIPT_TRANSPORT_ATTEMPTS):
        try:
            raw = await _generate_with_gemini(image_bytes, content_type, prompt)
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001 - 전송 계층 오류는 모두 재시도 대상
            last_exc = exc
    if last_exc is not None:
        raise ReceiptExtractionFailed(str(last_exc)) from last_exc

    extraction = _validate_extraction(raw)
    if extraction is None:
        raise ReceiptExtractionFailed("model output failed schema validation")

    if extraction.status == "ok":
        extraction = extraction.model_copy(update={"items": scrub_items(extraction.items)})
    return extraction


__all__ = [
    "PHONE_PATTERN",
    "VET_RECEIPT_MAX_OUTPUT_TOKENS",
    "VET_RECEIPT_MODEL_ID",
    "VET_RECEIPT_PROMPT_VERSION",
    "VET_RECEIPT_TRANSPORT_ATTEMPTS",
    "ReceiptExtraction",
    "ReceiptExtractionFailed",
    "ReceiptItem",
    "VetReasonCode",
    "build_receipt_prompt",
    "extract",
    "receipt_generation_config",
    "scrub_items",
]
