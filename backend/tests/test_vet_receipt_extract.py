"""영수증 추출 계약 (#353). **Gemini 를 부르지 않는다** — 모양과 스크러버만 본다."""

from typing import get_args

import pytest
from pydantic import ValidationError

from daengs_backend.models import VET_REASON_CODES
from daengs_backend.services import vet_receipt
from daengs_backend.services.vet_receipt import (
    ReceiptExtraction,
    ReceiptExtractionFailed,
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


def test_prompt_states_preventive_wins_over_targeted_system():
    """R1 — 예방이 이긴다. 실제 영수증의 `*주사-비오칸엠-곰팡이피부접종-20%` 이 이 규칙이
    없으면 `skin` 으로 잘못 떨어진다."""
    prompt = build_receipt_prompt()
    assert "Preventive care wins over the body system it happens to target" in prompt
    assert '"vaccination", not "skin"' in prompt


def test_prompt_states_preventive_falls_back_to_body_system_when_no_code_fits():
    """R2 — 예방인데 넷 중 맞는 코드가 없으면(스케일링) 신체계통으로 내려간다."""
    prompt = build_receipt_prompt()
    assert "fall back to the body-system code instead" in prompt
    assert 'so it becomes "dental"' in prompt


def test_prompt_states_date_range_takes_start_date():
    """R3 — `2019-05-17 ~ 2019-05-17` 처럼 범위로 찍히면 시작일이 `visited_on` 이다."""
    prompt = build_receipt_prompt()
    assert "visited_on is the start date of the range" in prompt


def test_prompt_states_amount_is_post_discount():
    """R4 — 수량/할인/금액 열이 있으면 할인 열이 아니라 금액(할인 반영 후) 열을 읽는다."""
    prompt = build_receipt_prompt()
    assert "total_krw and every item's amount_krw are the post-discount amount column" in prompt
    assert "never the discount column" in prompt


# 아래 셋은 압구정동물병원(2019-05-17) 실제 영수증의 문자열을 그대로 쓴 회귀 핀이다 —
# 규칙을 못 고치게 잡아 두는 것이지 이 커밋이 만드는 동작이 아니다.


def test_scrubber_keeps_real_receipt_item_names_untouched():
    """`곰팡이피부접종` 같은 실제 항목명이 스크러버에 오검열되면 §2 의 R1 예시가
    확인 화면에서 사라진다."""
    items = [
        ReceiptItem(name="*주사-비오칸엠-곰팡이피부접종-20%", amount_krw=12000),
        ReceiptItem(name="일반조제-1일", amount_krw=3000),
        ReceiptItem(name="진료비,진찰료", amount_krw=15000),
    ]
    assert scrub_items(items) == items


def test_scrubber_redacts_real_business_registration_number():
    got = scrub_items([ReceiptItem(name="850-61-00139 압구정동물병원", amount_krw=15000)])
    assert got[0].name == "<redacted>"
    assert got[0].amount_krw == 15000


def test_scrubber_redacts_real_receipt_number():
    got = scrub_items([ReceiptItem(name="000025003 영수증번호", amount_krw=15000)])
    assert got[0].name == "<redacted>"
    assert got[0].amount_krw == 15000


def test_hospital_phone_accepts_real_hospital_number():
    assert (
        ReceiptExtraction(
            status="ok", total_krw=1000, hospital_phone="02-547-7588"
        ).hospital_phone
        == "02-547-7588"
    )


def test_hospital_phone_rejects_real_business_registration_number():
    """`850-61-00139` 은 사업자등록번호 모양이지 전화번호가 아니다 — `{2,4}-{3,4}-{3,4}` 의
    가운데 묶음이 2자리라 이 칸에 못 앉는다."""
    with pytest.raises(ValidationError):
        ReceiptExtraction(status="ok", total_krw=1000, hospital_phone="850-61-00139")


# ── M1: 전송 오류는 한 번만 재시도한다 (docs §2 "못 읽었을 때") ─────────────


async def test_extract_retries_once_then_raises_after_two_transport_failures(monkeypatch):
    """정확히 두 번 시도한 뒤 `ReceiptExtractionFailed` 로 오른다 — 그 이상은 재시도하지
    않는다."""
    calls: list[int] = []

    async def _always_fails(*_args, **_kwargs):
        calls.append(1)
        raise TimeoutError("connection reset")

    monkeypatch.setattr(vet_receipt, "_generate_with_gemini", _always_fails)
    with pytest.raises(ReceiptExtractionFailed):
        await vet_receipt.extract(b"bytes", "image/jpeg")
    assert len(calls) == 2


async def test_extract_succeeds_on_second_attempt(monkeypatch):
    """첫 시도가 전송 오류로 죽어도, 두 번째 시도가 성공하면 정상 반환한다."""
    calls: list[int] = []

    async def _fails_then_succeeds(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("connection reset")
        return ReceiptExtraction(status="unreadable", unreadable_reason="blurry")

    monkeypatch.setattr(vet_receipt, "_generate_with_gemini", _fails_then_succeeds)
    result = await vet_receipt.extract(b"bytes", "image/jpeg")
    assert result.status == "unreadable"
    assert len(calls) == 2


async def test_extract_does_not_retry_a_valid_unreadable_answer(monkeypatch):
    """모델이 정상적으로 낸 `status="unreadable"` 은 실패가 아니다 — 재시도하지 않는다."""
    calls: list[int] = []

    async def _once(*_args, **_kwargs):
        calls.append(1)
        return ReceiptExtraction(status="unreadable", unreadable_reason="not_a_receipt")

    monkeypatch.setattr(vet_receipt, "_generate_with_gemini", _once)
    result = await vet_receipt.extract(b"bytes", "image/jpeg")
    assert result.status == "unreadable"
    assert len(calls) == 1
