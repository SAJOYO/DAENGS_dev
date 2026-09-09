"""영수증 추출 계약 (#353). **Gemini 를 부르지 않는다** — 모양과 스크러버만 본다."""

from typing import get_args

import pytest
from pydantic import ValidationError

from daengs_backend.models import VET_REASON_CODES
from daengs_backend.services.vet_receipt import (
    ReceiptExtraction,
    ReceiptItem,
    VetReasonCode,
    build_receipt_prompt,
    scrub_items,
)


def test_vet_reason_code_matches_vet_reason_codes():
    """`VetReasonCode` 는 `VET_REASON_CODES` 를 그대로 풀어 적은 것이다 —
    `Literal[*VET_REASON_CODES]` 가 아니라 명시적 리터럴이라야 스키마가 읽기 쉽다."""
    values = get_args(VetReasonCode)
    assert set(values) == set(VET_REASON_CODES)
    assert len(values) == len(VET_REASON_CODES)


def test_schema_has_no_field_for_personal_data():
    """**이 테스트가 §2 의 핵심 주장을 지킨다.** 개인정보는 "안 쓰기로 한 것" 이 아니라
    "쓸 칸이 없는 것" 이다. 칸이 생기면 제약 디코딩이 그것을 채울 수 있게 된다."""
    fields = set(ReceiptExtraction.model_fields)
    for banned in (
        "payer_name",
        "owner_name",
        "card_number",
        "business_number",
        "payer_phone",
        "customer_name",
        "raw_text",
    ):
        assert banned not in fields


def test_schema_has_no_is_oncology():
    """종양 여부는 임상 판단이라 유저만 켠다 (docs §2)."""
    assert "is_oncology" not in ReceiptExtraction.model_fields


def test_ok_requires_total():
    with pytest.raises(ValidationError):
        ReceiptExtraction(status="ok", total_krw=None)


def test_unreadable_requires_reason_and_empty_rest():
    with pytest.raises(ValidationError):
        ReceiptExtraction(status="unreadable")
    with pytest.raises(ValidationError):
        ReceiptExtraction(status="unreadable", unreadable_reason="blurry", total_krw=1000)


@pytest.mark.parametrize(
    "extra",
    [
        {"hospital_address": "서울시 강남구"},
        {"hospital_phone": "02-123-4567"},
        {"visited_on": "2026-09-01"},
        {"suggested_reason_code": "skin"},
        {"is_emergency": True},
    ],
)
def test_unreadable_rejects_every_other_field(extra):
    """이전 검증은 total_krw · items · hospital_name 만 봤다 — 나머지 칸도 비어야 한다."""
    with pytest.raises(ValidationError):
        ReceiptExtraction(status="unreadable", unreadable_reason="blurry", **extra)


def test_phone_rejects_card_shaped_value():
    """네 묶음은 전화번호가 아니다 — 카드번호가 이 칸에 앉는 것을 막는 그물."""
    with pytest.raises(ValidationError):
        ReceiptExtraction(
            status="ok", total_krw=1000, hospital_phone="5432-1234-5678-9012"
        )


def test_phone_accepts_korean_shapes():
    for good in ("02-123-4567", "031-1234-5678", "010-1234-5678", "1588-1234"):
        assert (
            ReceiptExtraction(status="ok", total_krw=1000, hospital_phone=good).hospital_phone
            == good
        )


@pytest.mark.parametrize(
    "dirty",
    [
        "홍길동 010-1234-5678",  # 개인 전화
        "123-45-67890 세금계산서",  # 사업자등록번호
        "5432-1234-5678 승인",  # 카드
        "승인번호 123456789012",  # 연속 숫자 8자리 이상
    ],
)
def test_scrubber_redacts_name_keeps_amount(dirty):
    """**항목을 통째로 버리지 않는다** — 합계가 안 맞으면 확인 화면이 거짓말을 한다."""
    got = scrub_items([ReceiptItem(name=dirty, amount_krw=15000)])
    assert got[0].name == "<redacted>"
    assert got[0].amount_krw == 15000


def test_scrubber_keeps_normal_items():
    items = [
        ReceiptItem(name="초진료", amount_krw=15000),
        ReceiptItem(name="피부소파검사", amount_krw=45000),
    ]
    assert scrub_items(items) == items


def test_prompt_lists_every_reason_code():
    """제안 코드가 프롬프트에 안 실리면 모델이 목록 밖의 값을 낸다."""
    prompt = build_receipt_prompt()
    for code in VET_REASON_CODES:
        assert code in prompt


def test_prompt_forbids_personal_data_in_item_names():
    prompt = build_receipt_prompt()
    assert "item names" in prompt
    assert "card number" in prompt
