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
VET_RECEIPT_PROMPT_VERSION = "vet-receipt-extract-v2"
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
    #: 이 항목이 몇 번째 `동물명` 블록에 있었나 (0부터). 블록이 하나뿐이거나 구분이
    #: 안 보이면 `null` 이다. **이름이 아니라 인덱스인 것이 요점이다** — 환자명을 담을
    #: 칸을 만들면 "개인정보는 칸이 없어서 안 나온다"(docs §2)가 그 자리에서 깨지고,
    #: `scrub_items` 는 **항목명만** 훑어서 그 칸으로 들어온 보호자 이름을 못 잡는다.
    patient_index: int | None = Field(default=None, ge=0, le=19)


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
    #: `동물명` 블록이 몇 개인가. 한 장에 여러 아이가 찍힌 영수증을 가른다 (docs §2 다견).
    #: **`is_emergency` 와 같은 결이다** — 임상 판단이 아니라 **인쇄된 글자를 세는 값**이라
    #: 모델이 잘한다. 1 이 기본이고, 2 이상이면 확인 화면이 금액을 나누게 한다.
    patient_count: int = Field(default=1, ge=1, le=20)

    @model_validator(mode="after")
    def shape_matches_status(self) -> "ReceiptExtraction":
        if self.status == "ok":
            if self.total_krw is None:
                raise ValueError("an ok extraction needs a total")
            return self
        if self.unreadable_reason is None:
            raise ValueError("an unreadable extraction needs a reason")
        if self.unreadable_reason == "no_amount":
            # `no_amount` 는 "읽었지만 합계가 없다" 다 — `blurry`/`not_a_receipt` 와 다르다.
            # 나머지 칸은 남아도 되지만 `total_krw` 만은 안 된다: 합계가 있으면 읽었다는
            # 뜻이라 애초에 이 사유가 아니어야 한다 (docs/vet-visits.md §2, 2026-09-10 실측).
            if self.total_krw is not None:
                raise ValueError("no_amount extraction cannot carry a total")
            return self
        if (
            self.total_krw is not None
            or self.items
            or self.hospital_name
            or self.hospital_address
            or self.hospital_phone
            or self.visited_on is not None
            or self.suggested_reason_code is not None
            or self.is_emergency
            # 새 칸을 여기 빠뜨리면 계약에 구멍이 난다 — `unreadable` 이 값을 들고
            # 지나간다. 대신 `_normalize_unreadable_extras` 가 검증 **전에** 눕히므로
            # 실제 모델 출력 때문에 여기서 터지지는 않는다 (그 함수 주석 참고).
            or self.patient_count != 1
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
_RECEIPT_RULE = """You read a Korean veterinary clinic receipt photo. Only transcribe what is actually printed on the receipt — never invent a value that is not there. total_krw comes only from a printed total line on the receipt — 합계, 총액, 청구금액, 받을금액, or the like — and never from anywhere else. Never compute total_krw: do not add up the line items, and do not derive it by any other arithmetic. A sum you calculated is not a total you read, even when it happens to match one. If no such printed total line is visible on the receipt — including when the photo is cropped or cut off before reaching it — output status="unreadable" with unreadable_reason="no_amount", even when every individual item's amount_krw was legible. Never write the payer's name, a personal phone number, a card number, or a business registration number anywhere in the output, including inside item names — if such a string appears next to an item, drop that part and keep only the item description. Amounts are integers in Korean won; strip commas and the "원" suffix, digits only. When a receipt breaks the amount into quantity / discount / amount columns plus a discount-total line, total_krw and every item's amount_krw are the post-discount amount column — never the discount column; the discount itself has no field and must not be captured anywhere. When the printed date is a range such as "2019-05-17 ~ 2019-05-17", visited_on is the start date of the range, even for a hospitalization stay where the two ends differ. hospital_phone is the clinic's own front-desk number, not the payer's — digits and hyphens only. suggested_reason_code must be one of the listed codes or null: when the items alone do not clearly point to one reason, output null (this leaves the confirmation screen with nothing pre-selected, which is the point) — reserve "other" for when the reason is clear but not in the list. Preventive care wins over the body system it happens to target: when an item is a vaccination or a preventive medication, use the matching preventive code even if the item also targets a body system — a ringworm vaccine that treats the skin is still "vaccination", not "skin", because owners ask "how much on prevention" and "how much because it was sick" as two separate questions. When a preventive act has no matching preventive code, fall back to the body-system code instead — a routine dental scaling is preventive but fits none of "vaccination" / "parasite_prevention" / "checkup" / "neuter", so it becomes "dental". Set is_emergency to true only when a line item such as a night-treatment fee, an emergency-treatment fee, or a holiday surcharge is actually printed — never because the treatment merely sounds urgent; this field records printed text, not judgment. When a checkup produced a targeted treatment or test on the same receipt, the body-system code wins over "checkup" — "checkup" is only for a checkup that is not followed by anything. When one visit lists several problems for an older dog, pick the single body system with the largest cost. At the mouth boundary, teeth and gums are "dental" while anything at or below the esophagus is "digestive". At the ear boundary, the outer ear and ear canal are "ear" while the inner ear and vestibular system are "neurologic". Heartworm prevention medication is "parasite_prevention" while treatment of an active heartworm infection is "cardiac" — an instance of the preventive-wins rule above. Neuter surgery and pyometra treatment open the same organs and look alike on a receipt — the diagnostic and procedure line items decide which. A Korean veterinary receipt may cover several animals on one page: it prints a section header naming the patient — "동물명", "환자명", or the like, sometimes bracketed — and the line items for that animal follow underneath, then the next header begins the next animal. patient_count is how many such patient headers are printed, counted in printed order, and every item's patient_index is the 0-based position of the header it sits under. Count only headers that name the animal: "보호자", "고객", "담당", and a veterinarian's name are not patients. When the receipt prints no patient header at all, or only one, patient_count is 1 and every patient_index is null. When a header is present but you cannot tell which header an item belongs to, leave that item's patient_index null rather than guessing. When it is genuinely unclear whether a second header names another animal, prefer the higher patient_count — a confirmation screen that asks one extra question costs far less than a bill silently attributed to the wrong dog. Never output the patient names themselves, anywhere, including inside item names — the count and the index are the whole output. Do not produce a per-patient total: patient_index is something you read off the page, not something you calculate, and the rule above against computing totals applies here too. total_krw remains the single printed total for the whole receipt even when several animals share it. If the image is not a receipt, or blurry, output status="unreadable" with the matching reason instead."""


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


def _normalize_ok_without_total(parsed: object) -> object:
    """모델이 `status="ok"` 인데 `total_krw` 가 없을 때를 `unreadable`/`no_amount` 로
    옮긴다. 프롬프트가 "계산하지 말라"만 지켜서 총액 없이 `ok` 를 내는 실측 사례가
    있다 — `shape_matches_status` 는 그대로 두고 여기서 계약 모양에 맞춘다.

    **나머지 칸은 지우지 않는다** — `no_amount` 는 "읽었지만 합계가 없다" 다. 총액 줄이
    잘려 나간 사진(가장 흔한 실패 형태)에서도 병원·날짜·항목은 이미 다 읽혀 있는데, 여기서
    비우면 확인 화면이 그것까지 버려서 유저가 다시 타이핑해야 한다 (2026-09-10 실측 —
    압구정동물병원 영수증, 총액 줄만 잘림). `total_krw` 만 지운다."""
    if not isinstance(parsed, dict):
        return parsed
    if parsed.get("status") != "ok":
        return parsed
    if parsed.get("total_krw") is not None:
        return parsed
    log.warning(
        "vet receipt extraction: model returned status=ok with no total_krw — "
        "normalizing to unreadable/no_amount"
    )
    normalized = dict(parsed)
    normalized["status"] = "unreadable"
    normalized["unreadable_reason"] = "no_amount"
    normalized["total_krw"] = None
    return normalized


def _normalize_unreadable_extras(parsed: object) -> object:
    """`unreadable` 인데 다견 칸이 실려 온 것을 **검증 전에** 눕힌다.

    `shape_matches_status` 는 `blurry`·`not_a_receipt` 가 아무것도 안 들고 오기를
    요구하는데, 모델은 사진을 못 읽으면서도 `patient_count` 를 2 로 낼 수 있다.
    그대로 검증에 넣으면 `ValidationError` → `_validate_extraction` 이 `None` →
    `ReceiptExtractionFailed` → **`status="failed"`** 가 된다.

    **`failed` 는 `unreadable/blurry` 보다 나쁘다.** `extract_draft` 는 failed 를
    저장하지 않고 돌아가므로 `extracted_at` 이 안 찍히고, 멱등 ③ 이 안 걸려 유저가
    다시 누를 때마다 **Gemini 를 또 부른다.** 흐린 사진은 계속 흐리므로 같은 자리를
    맴돈다. 화면 문구도 "사진이 흐려요"가 아니라 "우리 쪽 문제"가 된다.

    그래서 계약(위 검증기)은 그대로 지키되, 실제 출력이 거기서 터지지 않게 여기서
    눕힌다 — `_normalize_ok_without_total` 과 같은 자리·같은 이유다.
    """
    if not isinstance(parsed, dict):
        return parsed
    if parsed.get("status") != "unreadable":
        return parsed
    if parsed.get("unreadable_reason") == "no_amount":
        # `no_amount` 는 "읽었지만 합계가 없다" 라 나머지 칸을 들고 와도 된다.
        return parsed
    if parsed.get("patient_count", 1) == 1:
        return parsed
    normalized = dict(parsed)
    normalized["patient_count"] = 1
    return normalized


def _validate_extraction(raw: object) -> ReceiptExtraction | None:
    parsed: object = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(parsed, ReceiptExtraction):
        return parsed
    parsed = _normalize_ok_without_total(parsed)
    parsed = _normalize_unreadable_extras(parsed)
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
