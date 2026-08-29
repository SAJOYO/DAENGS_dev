"""`pet.restrictions` 판독표가 원문 전수를 덮고, 조건부를 무조건으로 눕히지 않는가.

Decision: #70

**왜 필요한가**: 이 표는 판단을 담는다. 판단은 조용히 틀리므로 두 가지를 잠근다.

  전수 배정   291종 **전부**가 상태 하나를 받아야 한다. 빈도 커버리지가 아니다 —
              `맹견류 입장 불가`(8행)·`접종 완료 필수`(6행)는 행 기준 0.03% 지만
              놓치면 사고가 나는 쪽이다. 안전 조건은 빈도와 중요도가 반비례한다
  조건 보존   `대형견 입마개, 목줄` 이 `[입마개(모두), 목줄(모두)]` 로 눕으면
              2kg 개 보호자가 "입마개 필요" 를 본다. 그 눕힘을 여기서 막는다

픽스처(`fixtures/kcisa_restrictions.json`)는 2026-08-27 스냅샷의 유정보 문자열 전수이며
**DB 없이** 검증한다 — CI 에 KCISA 적재본이 없기 때문이고, 동시에 이 파일이 나중에
Rule/LLM 추출기를 재는 gold set 이 된다.
"""

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from app.place.restriction_map import (
    KNOWN_CODES,
    LABELS,
    NON_INFORMATIVE,
    SUBJECT_LABELS,
    Certainty,
    ParseState,
    Subject,
    label_of,
    mapped_texts,
    read,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "kcisa_restrictions.json"


def _gold() -> list[dict]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))["strings"]


def test_fixture_is_the_measured_snapshot():
    """픽스처가 조용히 바뀌면 아래 전수 검사가 의미를 잃는다."""
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert payload["distinct"] == 291
    assert payload["total_rows"] == 1740
    assert len(payload["strings"]) == 291


def test_every_string_gets_a_state():
    """**전수 배정 100%.** 빈도가 낮다고 빠뜨리지 않는다."""
    missing = [entry["text"] for entry in _gold() if read(entry["text"]) is None]
    assert not missing, (
        f"{len(missing)}종이 판독되지 않는다. 표에 추가하거나 명시적으로 "
        f"unrepresentable 로 적어라:\n" + "\n".join(f"  {text}" for text in missing[:20])
    )


def test_table_has_no_entries_outside_the_snapshot():
    """표가 스냅샷에 없는 문자열을 갖고 있으면 오타이거나 죽은 항목이다."""
    stale = mapped_texts() - {entry["text"] for entry in _gold()}
    assert not stale, f"스냅샷에 없는 항목: {sorted(stale)}"


def test_non_informative_values_are_not_in_the_table():
    """`제한사항 없음`·`해당없음` 은 판독 대상이 아니라 상태다."""
    assert not (mapped_texts() & NON_INFORMATIVE)


def test_every_predicate_uses_a_declared_code_and_has_a_label():
    unknown_codes: set[str] = set()
    unlabeled: set[str] = set()
    for entry in _gold():
        reading = read(entry["text"])
        assert reading is not None
        for predicate in reading.predicates:
            if predicate.code not in KNOWN_CODES:
                unknown_codes.add(predicate.code)
            if predicate.code not in LABELS:
                unlabeled.add(predicate.code)
    assert not unknown_codes, f"어휘 밖 코드: {sorted(unknown_codes)}"
    assert not unlabeled, f"라벨 없는 코드: {sorted(unlabeled)}"


def test_every_subject_has_a_qualifier_label():
    assert set(SUBJECT_LABELS) == set(Subject)


def test_raw_only_entries_carry_a_reason():
    """`raw_only` 는 '아직 안 했다' 가 아니라 '일부러 안 한다' 여야 한다."""
    for entry in _gold():
        reading = read(entry["text"])
        assert reading is not None
        if reading.parse_state is ParseState.RAW_ONLY:
            assert reading.reason is not None, entry["text"]
            assert not reading.predicates, entry["text"]


def test_mapped_entries_have_predicates():
    for entry in _gold():
        reading = read(entry["text"])
        assert reading is not None
        if reading.parse_state is not ParseState.RAW_ONLY:
            assert reading.predicates, f"술어가 없는데 raw_only 가 아니다: {entry['text']}"


# ------------------------------------------------------------------ 골든 케이스
# 난이도 밴드별 함정. 나중에 Rule/LLM 추출기를 잴 때 이 케이스들이 기준이 된다
# (docs/research/2026-08-27-tag-material.md §5 의 단순/복합/크기조건/구역조건).
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 단순
        ("목줄", (("require:leash", Subject.ALL),)),
        ("케이지 이용", (("require:carrier", Subject.ALL),)),
        # 복합 — 콤마가 AND 다
        ("목줄, 배변봉투", (("require:leash", Subject.ALL), ("require:poop_bag", Subject.ALL))),
        # 크기 조건부 — **여기가 핵심.** 목줄은 모두에게, 입마개는 대형견에게만
        (
            "대형견 입마개, 목줄",
            (("require:muzzle", Subject.SIZE_LARGE), ("require:leash", Subject.ALL)),
        ),
        ("대형견은 목줄", (("require:leash", Subject.SIZE_LARGE),)),
        ("맹견은 입마개 필수", (("require:muzzle", Subject.BREED_GUARD),)),
        ("수컷 매너벨트 필수", (("require:manner_belt", Subject.SEX_MALE),)),
        ("중성화 안 한 경우 매너벨트 착용", (("require:manner_belt", Subject.INTACT),)),
        # 구역 조건부
        (
            "실내 목줄 필수",
            (("require:leash", Subject.ALL), ("zone:indoor_partial", Subject.ALL)),
        ),
        ("야외만 반려동물 동반 가능", (("zone:outdoor_only", Subject.ALL),)),
        # 나이 — 프로필로 판정 가능한 축이다
        ("10살 이상 불가", (("deny:age", Subject.AGE_SENIOR),)),
        ("4개월 미만 입장 불가", (("deny:age", Subject.AGE_PUPPY),)),
        # 종 — 이 데이터셋에는 고양이 전용 시설이 있다
        ("고양이 전용", (("deny:species_dog", Subject.ALL),)),
    ],
)
def test_golden_readings(text: str, expected: tuple[tuple[str, Subject], ...]):
    reading = read(text)
    assert reading is not None, text
    assert tuple((p.code, p.applies_to) for p in reading.predicates) == expected


def test_conditional_predicates_are_not_flattened_to_all():
    """`대형견 입마개` 가 모두에게 적용되면 소형견 보호자에게 없는 제한을 보여준다."""
    reading = read("대형견 입마개, 목줄")
    assert reading is not None
    muzzle = next(p for p in reading.predicates if p.code == "require:muzzle")
    assert muzzle.applies_to is Subject.SIZE_LARGE


def test_conditional_grants_are_marked_partial():
    """`X 하면 Y 가능` 은 술어가 담지 못한다 — UI 가 원문을 함께 보여야 한다."""
    for text in (
        "케이지 또는 안고 있으면 동반 가능",
        "야외만 동반 가능, 실내는 안고 있으면 동반 가능",
        "목줄 착용 시 대형견도 입장 가능",
    ):
        reading = read(text)
        assert reading is not None, text
        assert reading.parse_state is ParseState.PARTIAL, text


def test_labels_carry_the_subject_qualifier():
    reading = read("대형견 입마개, 목줄")
    assert reading is not None
    labels = [label_of(p) for p in reading.predicates]
    assert labels == ["입마개·대형견", "목줄"]


def test_unknown_text_is_not_guessed():
    """재적재로 처음 보는 문장이 들어와도 추측하지 않는다."""
    assert read("이 문장은 표에 없다") is None


def test_coverage_report_is_reported_for_review():
    """행 커버리지는 **완료 조건이 아니라 결과 지표**다 (전수 배정이 조건이다).

    수치가 크게 떨어지면 표가 퇴화한 것이므로 하한만 둔다.
    """
    states: Counter[str] = Counter()
    rows: Counter[str] = Counter()
    for entry in _gold():
        reading = read(entry["text"])
        assert reading is not None
        states[reading.parse_state] += 1
        rows[reading.parse_state] += entry["rows"]
    total_rows = sum(rows.values())
    fully_mapped = rows[ParseState.MAPPED] / total_rows
    assert fully_mapped >= 0.90, (
        f"mapped 행 비율 {fully_mapped:.1%} — 문자열 {dict(states)} · 행 {dict(rows)}"
    )


# ------------------------------------------------------------------ 수치 보존
# **리뷰 지적 ④.** `mapped` 는 "술어가 원문을 다 담았다" 는 뜻인데, 원문의 수치를
# 버리고도 `mapped` 이면 그 선언이 거짓말이 된다. 그리고 이 표를 gold set 으로 쓸 때
# 정확히 읽은 파서와 대충 읽은 파서가 같은 점수를 받는다.
_NUMERIC_PATTERNS = (
    (re.compile(r"최대\s*\d+\s*마리|\d+\s*마리까지"), "limit:max_dogs", "max"),
    (re.compile(r"\d+\s*개월\s*(?:미만|이하|이상)"), "deny:age", "max_months"),
    (re.compile(r"\d+\s*(?:살|세)\s*이상"), "deny:age", "min_years"),
    (re.compile(r"\d+\s*kg\s*(?:초과|이상)", re.IGNORECASE), "deny:size", "min_kg"),
)


def test_numeric_sources_keep_their_value():
    """원문에 수치가 있으면 술어가 그 값을 들고 있어야 한다."""
    lossy: list[str] = []
    for entry in _gold():
        text = entry["text"]
        reading = read(text)
        assert reading is not None
        for pattern, code, key in _NUMERIC_PATTERNS:
            if not pattern.search(text):
                continue
            carriers = [p for p in reading.predicates if p.code == code]
            if carriers and not any(p.param(key) for p in carriers):
                lossy.append(f"{text}  ({code} 에 {key} 없음)")
    assert not lossy, "수치를 버린 항목:\n" + "\n".join(f"  {item}" for item in lossy)


def test_max_dogs_values_are_distinguishable():
    """`최대 2마리` 와 `최대 5마리` 가 같은 술어가 되면 안 된다."""
    two = next(p for p in read("객실당 최대 2마리").predicates if p.code == "limit:max_dogs")
    five = next(p for p in read("객실당 최대 5마리").predicates if p.code == "limit:max_dogs")
    assert two.int_param("max") == 2
    assert five.int_param("max") == 5


def test_hedged_sources_are_marked_soft():
    """단정하지 않은 원문은 `soft` 여야 판정기가 배제로 승격하지 않는다."""
    for text in ("노견일 경우 미용 어려울 수 있음", "10살 이상 노령견 신규예약 불가"):
        reading = read(text)
        assert reading is not None, text
        age = [p for p in reading.predicates if p.code == "deny:age"]
        assert age, text
        assert all(p.certainty is Certainty.SOFT for p in age), text


def test_plain_denials_stay_firm():
    reading = read("10살 이상 불가")
    age = next(p for p in reading.predicates if p.code == "deny:age")
    assert age.certainty is Certainty.FIRM
