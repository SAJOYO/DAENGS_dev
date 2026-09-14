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


def test_unreadable_no_amount_is_valid_shape_with_no_total():
    """합계 줄이 안 보이면(사진이 그 앞에서 잘렸어도) 항목 금액을 다 읽었더라도
    `total_krw` 없이 `unreadable`/`no_amount` 여야 한다 — 압구정동물병원 실측(2026-09-10)."""
    extraction = ReceiptExtraction(status="unreadable", unreadable_reason="no_amount")
    assert extraction.total_krw is None
    assert extraction.items == []


def test_unreadable_no_amount_may_carry_everything_but_total():
    """`no_amount` 는 "읽었지만 합계가 없다" 다 — `blurry`/`not_a_receipt` 와 달리
    나머지 필드는 채워도 된다. 이 규칙 반전이 이번 변경의 핵심이다 (docs §2, 2026-09-10)."""
    extraction = ReceiptExtraction(
        status="unreadable",
        unreadable_reason="no_amount",
        visited_on="2019-05-17",
        hospital_name="압구정동물병원",
        hospital_address="서울 강남구 압구정로 224",
        hospital_phone="02-547-7588",
        items=[{"name": "진료비,진찰료", "amount_krw": 5500}],
        suggested_reason_code="skin",
        is_emergency=False,
    )
    assert extraction.total_krw is None
    assert extraction.hospital_phone == "02-547-7588"
    assert len(extraction.items) == 1


def test_unreadable_no_amount_still_rejects_a_total():
    """`no_amount` 인데 `total_krw` 가 있으면 모순이다 — 합계를 읽었으면 그 사유가 아니다."""
    with pytest.raises(ValidationError):
        ReceiptExtraction(status="unreadable", unreadable_reason="no_amount", total_krw=1000)


@pytest.mark.parametrize("reason", ["blurry", "not_a_receipt"])
@pytest.mark.parametrize(
    "extra",
    [
        {"hospital_address": "서울시 강남구"},
        {"hospital_phone": "02-123-4567"},
        {"visited_on": "2026-09-01"},
        {"suggested_reason_code": "skin"},
        {"is_emergency": True},
        {"patient_count": 2},
    ],
)
def test_unreadable_rejects_every_other_field(reason, extra):
    """`blurry`·`not_a_receipt` 는 여전히 나머지 칸도 비어야 한다 — `no_amount` 만 풀렸다는
    회귀를 여기서 잡는다 (이번 변경의 회귀 위험 지점)."""
    with pytest.raises(ValidationError):
        ReceiptExtraction(status="unreadable", unreadable_reason=reason, **extra)


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


def test_prompt_forbids_computing_total_from_items():
    """total_krw 는 계산이 아니라 인쇄된 합계 줄에서만 온다 — 압구정동물병원 실측(2026-09-10):
    항목 5,500+46,200+10,000 을 더해 61,700 을 합계처럼 낸 사고가 이 규칙의 이유다."""
    prompt = build_receipt_prompt()
    assert "total_krw comes only from a printed total line on the receipt" in prompt
    assert "Never compute total_krw" in prompt
    assert "do not add up the line items" in prompt
    assert "A sum you calculated is not a total you read" in prompt


def test_prompt_states_cropped_total_line_is_no_amount():
    """합계 줄이 사진에서 잘려 안 보이면 항목 금액을 다 읽었어도 no_amount 다."""
    prompt = build_receipt_prompt()
    assert 'output status="unreadable" with unreadable_reason="no_amount"' in prompt
    assert "even when every individual item's amount_krw was legible" in prompt


# ── 다견 영수증 ──────────────────────────────────────────────────────


def test_schema_has_no_field_for_patient_names():
    """**아이를 가르는 값은 인덱스지 이름이 아니다.** 환자명을 담을 칸을 만들면 §2 의
    "개인정보는 칸이 없어서 안 나온다"가 그 자리에서 깨지고, `scrub_items` 는 항목명만
    훑어서 그 칸으로 들어온 보호자 이름을 못 잡는다."""
    fields = set(ReceiptExtraction.model_fields) | set(ReceiptItem.model_fields)
    for banned in ("patient_name", "patient_names", "animal_name", "pet_name", "names"):
        assert banned not in fields
    assert "patient_count" in ReceiptExtraction.model_fields
    assert "patient_index" in ReceiptItem.model_fields


def test_single_patient_is_the_default():
    """한 마리가 특수 케이스가 아니라 기본값이다 — 영수증 대부분이 이쪽이다."""
    extraction = ReceiptExtraction(status="ok", total_krw=1000)
    assert extraction.patient_count == 1
    assert ReceiptItem(name="진찰료", amount_krw=1000).patient_index is None


def test_items_carry_the_block_they_sat_under():
    """다견 영수증은 `동물명` 블록이 반복되고 항목이 블록 안에 갈린다 (실측 2026-09-14)."""
    extraction = ReceiptExtraction(
        status="ok",
        total_krw=191_300,
        patient_count=2,
        items=[
            {"name": "*광견병백신(관납)", "amount_krw": 10000, "patient_index": 0},
            {"name": "*검사-귀-도말", "amount_krw": 20000, "patient_index": 0},
            {"name": "소염위생관리", "amount_krw": 15000, "patient_index": 1},
        ],
    )
    assert [i.patient_index for i in extraction.items] == [0, 0, 1]


def test_blurry_extraction_cannot_carry_a_patient_count():
    """`unreadable` 은 아무것도 안 들고 온다 — 새 칸도 예외가 아니다."""
    with pytest.raises(ValidationError):
        ReceiptExtraction(status="unreadable", unreadable_reason="blurry", patient_count=2)


def test_no_amount_may_carry_a_patient_count():
    """`no_amount` 는 "읽었지만 합계가 없다" 라 나머지 칸은 살아 있다 — 총액 줄만 잘린
    다견 영수증에서 블록 수까지 버리면 확인 화면이 손해를 본다."""
    extraction = ReceiptExtraction(
        status="unreadable", unreadable_reason="no_amount", patient_count=2
    )
    assert extraction.patient_count == 2


def test_validate_lays_down_patient_count_on_blurry_instead_of_failing():
    """**이 테스트가 `failed` 로 떨어지는 회귀를 막는다.** 모델이 사진을 못 읽으면서도
    `patient_count` 를 2 로 내면, 그대로 검증에 넣을 때 `ValidationError` → `failed` 가
    된다. `failed` 는 저장이 안 돼 멱등 ③ 이 안 걸리고, 유저가 다시 누를 때마다 Gemini 를
    또 부른다 — 흐린 사진은 계속 흐리므로 같은 자리를 맴돈다."""
    got = vet_receipt._validate_extraction(
        {"status": "unreadable", "unreadable_reason": "blurry", "patient_count": 2}
    )
    assert got is not None, "눕히지 않으면 None 이 되어 failed 로 간다"
    assert got.status == "unreadable"
    assert got.unreadable_reason == "blurry"
    assert got.patient_count == 1


def test_prompt_states_patient_blocks_are_counted_not_named():
    prompt = build_receipt_prompt()
    assert "동물명" in prompt
    assert "Never output the patient names themselves" in prompt


def test_prompt_excludes_guardian_and_vet_from_the_patient_count():
    """보호자·담당 수의사 이름이 블록으로 세어지면 1마리 집에서도 분할을 묻는다."""
    prompt = build_receipt_prompt()
    assert "보호자" in prompt and "고객" in prompt
    assert "are not patients" in prompt


def test_prompt_forbids_a_per_patient_total():
    """블록별 소계는 영수증에 **안 찍혀 있다** (실측). 모델이 더하기 시작하면
    `33bfe0ec` 이 막아 둔 "합계는 읽는 것이지 계산하는 것이 아니다"가 다시 샌다."""
    prompt = build_receipt_prompt()
    assert "Do not produce a per-patient total" in prompt
    assert "not something you calculate" in prompt


def test_prompt_prefers_the_higher_count_when_unsure():
    """거짓 양성은 확인 한 번이고, 거짓 음성은 누계가 조용히 틀린다."""
    prompt = build_receipt_prompt()
    assert "prefer the higher patient_count" in prompt


def test_real_multi_pet_receipt_blocks_sum_to_the_printed_total():
    """실측 2026-09-14 — 두 블록의 항목 합이 청구 금액과 **오차 0원**이다. 이것이
    "합이 안 맞으면 항목을 놓쳤다"는 검산의 근거다."""
    block_a = [10000, 10000, 11000, 20000, 11000, 6600, 24500, 1100, 15000]
    block_b = [10000, 15000, 10000, 11000, 6600, 24500, 5000]
    assert sum(block_a) == 109_200
    assert sum(block_b) == 82_100
    assert sum(block_a) + sum(block_b) == 191_300


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


# ── M2: status="ok" 인데 total_krw 가 없으면 no_amount 로 정규화한다 ───────────
# 압구정동물병원(2019-05-17) 실측 원본 payload 그대로 — 프롬프트 규칙은 지켰지만
# (계산 안 함) status 를 unreadable 로 안 바꾼 사고.

_REAL_OK_WITHOUT_TOTAL = {
    "status": "ok",
    "visited_on": "2019-05-17",
    "hospital_name": "압구정동물병원",
    "hospital_address": "서울 강남구 압구정로 224",
    "hospital_phone": "02-547-7588",
    "items": [
        {"name": "진료비,진찰료", "amount_krw": 5500},
        {"name": "일반조제-1일", "amount_krw": 46200},
        {"name": "주사-비오칸엠-곰팡이피부접종-20%", "amount_krw": 10000},
    ],
    "suggested_reason_code": "skin",
    "is_emergency": False,
}


def test_validate_extraction_normalizes_ok_without_total_to_no_amount():
    """`status="ok"` 인데 `total_krw` 가 없으면(누락이든 null 이든) `unreadable`/
    `no_amount` 로 바뀐다 — `total_krw` 만 지워지고, 모델이 이미 읽은 나머지 필드는
    **살아남는다**. 이것이 `ee18dd5` 의 전신 동작(모두 삭제)을 뒤집는 지점이다 —
    총액 줄만 잘린 사진에서 이미 읽힌 병원·날짜·항목까지 버릴 이유가 없다
    (docs §2, 2026-09-10 실측)."""
    extraction = vet_receipt._validate_extraction(_REAL_OK_WITHOUT_TOTAL)
    assert extraction is not None
    assert extraction.status == "unreadable"
    assert extraction.unreadable_reason == "no_amount"
    assert extraction.total_krw is None
    assert extraction.hospital_phone == "02-547-7588"
    assert len(extraction.items) == 3
    assert extraction.hospital_name == "압구정동물병원"
    assert extraction.hospital_address == "서울 강남구 압구정로 224"
    assert str(extraction.visited_on) == "2019-05-17"
    assert extraction.suggested_reason_code == "skin"
    assert extraction.is_emergency is False


def test_validate_extraction_leaves_ok_with_total_untouched():
    """총액이 있는 `ok` 는 정규화의 대상이 아니다."""
    payload = dict(_REAL_OK_WITHOUT_TOTAL, total_krw=61700)
    extraction = vet_receipt._validate_extraction(payload)
    assert extraction is not None
    assert extraction.status == "ok"
    assert extraction.total_krw == 61700
    assert extraction.hospital_name == "압구정동물병원"
    assert len(extraction.items) == 3


def test_validate_extraction_leaves_model_reported_unreadable_untouched():
    """모델이 스스로 낸 `unreadable` 은 이유를 건드리지 않는다."""
    extraction = vet_receipt._validate_extraction(
        {"status": "unreadable", "unreadable_reason": "blurry"}
    )
    assert extraction is not None
    assert extraction.status == "unreadable"
    assert extraction.unreadable_reason == "blurry"


async def test_extract_normalizes_real_ok_without_total_instead_of_raising(monkeypatch):
    """실측 원본 payload — `extract` 가 `ReceiptExtractionFailed` 로 오르지 않고
    `unreadable`/`no_amount` 를 정상적으로 반환해야 한다."""

    async def _returns_real_payload(*_args, **_kwargs):
        return dict(_REAL_OK_WITHOUT_TOTAL)

    monkeypatch.setattr(vet_receipt, "_generate_with_gemini", _returns_real_payload)
    result = await vet_receipt.extract(b"bytes", "image/jpeg")
    assert result.status == "unreadable"
    assert result.unreadable_reason == "no_amount"
    assert result.total_krw is None
    assert result.hospital_phone == "02-547-7588"
    assert len(result.items) == 3
