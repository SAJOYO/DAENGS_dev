"""저장 축 둘이 서로 다른 것을 말하는가 — `derive()` 의 계약.

Decision: #70

**왜 두 축인가**: 태그가 0개여도 사용자가 할 행동이 다른 네 상태가 있다.

    원문 없음        unknown / (parse 없음)     → 전화로 확인해야 한다
    "제한사항 없음"  none_confirmed / mapped    → 확인된 사실이다
    "해당없음"       not_applicable / mapped    → 입장 가능의 근거가 아니다
    못 읽은 문장     restricted / raw_only      → 원문을 보여주면 된다

한 값으로 합치면 "모름" 과 "제한 없음" 이 섞이고, 그게 미상을 무제한으로 읽는 사고다.
"""

import pytest

from app.place.restriction_map import (
    RESTRICTION_SEMANTICS_VERSION,
    ParseState,
    RestrictionState,
    Subject,
    derive,
)


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_missing_text_is_unknown_not_none(raw):
    """KTO 9,692행. 원문이 없는 것은 **제한이 없는 것이 아니다.**"""
    result = derive(raw)
    assert result.state is RestrictionState.UNKNOWN
    assert result.parse_state is None  # 파싱할 대상이 없다
    assert result.predicates == ()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("제한사항 없음", RestrictionState.NONE_CONFIRMED),
        ("해당없음", RestrictionState.NOT_APPLICABLE),
    ],
)
def test_explicit_zero_states_keep_their_distinct_meanings(raw, expected):
    """제한 없음과 제한란 해당 없음은 둘 다 명시값이지만 입장 가능의 근거는 전자뿐이다."""
    result = derive(raw)
    assert result.state is expected
    assert result.parse_state is ParseState.MAPPED
    assert result.predicates == ()


def test_unmapped_text_is_restricted_but_unread():
    """표에 없는 새 문장. 제한이 있는 것은 알지만 우리가 못 읽었다 — 추측하지 않는다."""
    result = derive("이 문장은 판독표에 없다")
    assert result.state is RestrictionState.RESTRICTED
    assert result.parse_state is ParseState.RAW_ONLY
    assert result.predicates == ()


def test_mapped_text_carries_predicates():
    result = derive("목줄, 배변봉투")
    assert result.state is RestrictionState.RESTRICTED
    assert result.parse_state is ParseState.MAPPED
    assert [p.code for p in result.predicates] == ["require:leash", "require:poop_bag"]


def test_conditional_subject_survives_derivation():
    """`applies_to` 가 저장까지 살아남아야 PR 4 가 소형견에게 입마개를 안 보여준다."""
    result = derive("대형견 입마개, 목줄")
    muzzle = next(p for p in result.predicates if p.code == "require:muzzle")
    assert muzzle.applies_to is Subject.SIZE_LARGE


def test_columns_carry_the_semantics_version():
    """버전 없는 파생값은 어느 규칙으로 만든 것인지 말하지 못한다."""
    columns = derive("목줄").to_columns()
    assert columns["restriction_semantics_version"] == RESTRICTION_SEMANTICS_VERSION
    assert columns["restriction_state"] == "restricted"
    assert columns["restriction_parse_state"] == "mapped"
    assert columns["restriction_predicates"] == [
        {"code": "require:leash", "applies_to": "all", "params": {}, "certainty": "firm"}
    ]


def test_unknown_rows_leave_parse_state_null_for_the_check_constraint():
    """리비전 0018 의 `parse_state_presence` 제약과 같은 규칙을 코드도 지킨다."""
    for raw, expects_parse in (
        (None, False), ("제한사항 없음", True), ("해당없음", True), ("목줄", True),
    ):
        columns = derive(raw).to_columns()
        assert (columns["restriction_parse_state"] is not None) is expects_parse
