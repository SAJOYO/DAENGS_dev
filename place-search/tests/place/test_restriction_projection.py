"""조건 술어를 이 개에 대고 볼 때 — 무엇을 숨기고 무엇을 판정하는가.

Decision: #70

**두 가지 다른 일을 한다.** 섞으면 안 된다.

    투영   `applies_to` 로 걸러 이 개에게 보여줄 칩을 정한다
    판정   그중 "못 간다" 가 증명되는 것만 `incompatible` 로 올린다

`require:muzzle@size:large` 는 대형견에게 **보이지만** 곧바로 못 가는 이유는 아니다.
반대로 실제로 준비했는지 모르므로 가능하다고 확정하지도 않는다.
"""

from app.place.contracts import RestrictionChip, RestrictionFacts
from app.place.restriction_projection import project


def _facts(
    *chips: tuple[str, str, str],
    state: str = "restricted",
    parse_state: str = "mapped",
) -> RestrictionFacts:
    return RestrictionFacts(
        state=state,
        parse_state=parse_state,
        chips=[
            RestrictionChip(code=code, label=label, applies_to=applies_to)
            for code, label, applies_to in chips
        ],
    )


MUZZLE_LARGE = ("require:muzzle", "입마개·대형견", "size:large")
LEASH_ALL = ("require:leash", "목줄", "all")
DENY_LARGE = ("deny:size", "크기 제한·대형견", "size:large")
DENY_SENIOR = ("deny:age", "나이 제한·노령견", "age:senior")
DENY_PUPPY = ("deny:age", "나이 제한·어린 개", "age:puppy")
DENY_DOG = ("deny:species_dog", "개 불가", "all")
CARRIER = ("require:carrier", "케이지", "all")
BEHAVIOR = ("deny:behavior", "성격 제한", "all")
VACCINATION = ("require:vaccination", "접종 필수", "all")


# ---------------------------------------------------------------- 투영
def test_conditional_chip_disappears_for_a_dog_it_does_not_apply_to():
    """2kg 개 보호자가 "입마개 필요" 를 보면 안 된다."""
    result = project(_facts(MUZZLE_LARGE, LEASH_ALL), dog_size="small", dog_age_years=3)
    assert [chip.code for chip in result.chips] == ["require:leash"]


def test_conditional_chip_stays_for_the_dog_it_applies_to():
    result = project(_facts(MUZZLE_LARGE, LEASH_ALL), dog_size="large", dog_age_years=3)
    assert [chip.code for chip in result.chips] == ["require:muzzle", "require:leash"]


def test_unknown_dog_size_keeps_every_chip():
    """숨기는 실수가 보여주는 실수보다 나쁘다 — 보호자는 본 것만 확인할 수 있다."""
    result = project(_facts(MUZZLE_LARGE, LEASH_ALL), dog_size=None, dog_age_years=None)
    assert len(result.chips) == 2


def test_subjects_the_profile_cannot_judge_are_kept():
    """수컷·중성화·생리·견종은 프로필로 못 가른다. 걸러내면 조건을 숨기는 것이다."""
    facts = _facts(
        ("require:manner_belt", "매너벨트·수컷", "sex:male"),
        ("deny:breed", "견종 제한·일부 견종", "breed:named"),
    )
    result = project(facts, dog_size="small", dog_age_years=2)
    assert len(result.chips) == 2


def test_medium_up_subject_covers_medium_and_large():
    facts = _facts(("require:muzzle", "입마개·중대형견", "size:medium_up"))
    assert project(facts, dog_size="medium", dog_age_years=3).chips
    assert project(facts, dog_size="large", dog_age_years=3).chips
    assert not project(facts, dog_size="small", dog_age_years=3).chips


# ---------------------------------------------------------------- 판정
def test_size_denial_blocks_only_the_denied_size():
    denied = project(_facts(DENY_LARGE), dog_size="large", dog_age_years=3)
    allowed = project(_facts(DENY_LARGE), dog_size="small", dog_age_years=3)

    assert denied.state == "incompatible"
    assert denied.reason == "size_denied"
    assert denied.blocking == ["deny:size"]
    # 소형견에게는 칩 자체가 안 보이므로 판정할 것도 없다.
    assert allowed.state == "compatible"
    assert allowed.chips == []


def test_age_rules_stay_unknown_until_the_source_threshold_is_preserved():
    """어린 개·노령견을 한 고정 나이로 발명하지 않는다."""
    for chip, age in ((DENY_PUPPY, 3), (DENY_SENIOR, 12)):
        result = project(_facts(chip), dog_size="small", dog_age_years=age)
        assert (result.state, result.reason) == ("unknown", "unresolved_condition")
        assert result.blocking == []


def test_explicit_dog_denial_blocks_without_more_profile_data():
    result = project(_facts(DENY_DOG), dog_size="small", dog_age_years=3)
    assert (result.state, result.reason) == ("incompatible", "species_denied")
    assert result.blocking == ["deny:species_dog"]


def test_unresolved_conditions_are_shown_without_claiming_compatibility():
    """장비·접종·행동 상태를 요청에서 모르므로 보이되 가능으로 확정하지 않는다."""
    result = project(
        _facts(MUZZLE_LARGE, CARRIER, VACCINATION, BEHAVIOR),
        dog_size="large",
        dog_age_years=3,
    )
    assert (result.state, result.reason) == ("unknown", "unresolved_condition")
    assert {chip.code for chip in result.chips} == {
        "require:muzzle", "require:carrier", "require:vaccination", "deny:behavior",
    }
    assert result.blocking == []


# ---------------------------------------------------------------- 미상
def test_unknown_restrictions_stay_unknown():
    result = project(
        RestrictionFacts(state="unknown"), dog_size="small", dog_age_years=3,
    )
    assert result.state == "unknown"
    assert result.reason == "restrictions_unknown"
    assert result.chips == []


def test_incomplete_parse_without_a_known_blocker_stays_unknown():
    partial = project(
        _facts(DENY_LARGE, parse_state="partial"),
        dog_size="medium",
        dog_age_years=3,
    )
    raw_only = project(
        _facts(parse_state="raw_only"), dog_size="medium", dog_age_years=3,
    )
    assert (partial.state, partial.reason) == ("unknown", "incomplete_restrictions")
    assert (raw_only.state, raw_only.reason) == ("unknown", "incomplete_restrictions")


def test_none_confirmed_is_compatible_with_no_chips():
    result = project(
        _facts(state="none_confirmed"), dog_size="large", dog_age_years=12,
    )
    assert result.state == "compatible"
    assert result.chips == []

    not_applicable = project(
        _facts(state="not_applicable"), dog_size="large", dog_age_years=12,
    )
    assert (not_applicable.state, not_applicable.reason) == (
        "unknown", "restrictions_not_applicable",
    )


def test_missing_facts_are_unknown_not_compatible():
    """아직 파생 안 된 행을 "조건 없음" 으로 읽으면 안 된다."""
    result = project(None, dog_size="small", dog_age_years=3)
    assert result.state == "unknown"


# ---------------------------------------------------------------- 회귀 (리뷰 지적)
def _chip(code: str, applies_to: str = "all", *, certainty: str = "firm", **params):
    return RestrictionChip(
        code=code, label=code, applies_to=applies_to,
        params={k: str(v) for k, v in params.items()}, certainty=certainty,
    )


def _one(chip: RestrictionChip) -> RestrictionFacts:
    return RestrictionFacts(state="restricted", parse_state="mapped", chips=[chip])


def test_puppy_limit_does_not_block_an_adult_dog():
    """**리뷰 지적 ②.** 5살 개가 "4개월 미만 입장 불가" 에 걸리던 오판정.

    원인은 `age:puppy` 가 `applies_to_dog` 에서 걸러지지 않고 통과한 뒤,
    판정 루프가 `code` 만 보고 나이가 있으면 바로 막은 것이다.
    """
    facts = _one(_chip("deny:age", "age:puppy", max_months=4))

    adult = project(facts, dog_size="small", dog_age_years=5.0)
    assert adult.state != "incompatible", "성견이 어린 개 제한에 걸린다"
    assert adult.chips == [], "해당 없는 조건이 칩으로 남았다"

    puppy = project(facts, dog_size="small", dog_age_years=0.2)
    assert puppy.state == "incompatible"
    assert puppy.reason == "age_denied"


def test_each_puppy_threshold_is_its_own():
    """원문 문턱이 3·4·5개월로 갈린다 — 하나로 뭉개면 세 문장이 구분 불가다."""
    three = _one(_chip("deny:age", "age:puppy", max_months=3))
    five = _one(_chip("deny:age", "age:puppy", max_months=5))
    age = 4.0 / 12.0  # 4개월

    assert project(three, dog_size="small", dog_age_years=age).state != "incompatible"
    assert project(five, dog_size="small", dog_age_years=age).state == "incompatible"


def test_senior_threshold_from_the_source_beats_the_default():
    """`8세 이상` 이라고 적힌 곳은 기본값 10 이 아니라 8 로 판정한다."""
    facts = _one(_chip("deny:age", "age:senior", min_years=8))
    assert project(facts, dog_size="small", dog_age_years=9.0).state == "incompatible"
    assert project(facts, dog_size="small", dog_age_years=7.0).state != "incompatible"


def test_soft_predicates_are_shown_but_never_block():
    """**리뷰 지적 ③.** "어려울 수 있음" 을 "이용 불가" 로 올리면 원문보다 강해진다.

    `compatible` 도 아니다 — 미해결 조건이 남아 있으므로 fail closed 해서 `unknown`
    이다. 우리가 못 푼 조건을 "가능" 이라고 부르지 않는다.
    """
    facts = _one(_chip("deny:age", "age:senior", min_years=10, certainty="soft"))
    result = project(facts, dog_size="small", dog_age_years=12.0)

    assert result.state == "unknown"
    assert result.reason == "unresolved_condition"
    assert result.chips, "판정은 안 해도 칩은 보여야 한다"


def test_firm_predicate_of_the_same_code_still_blocks():
    """확실성은 코드가 아니라 **원문마다** 다르다 — 같은 `deny:age` 가 갈린다."""
    firm = _one(_chip("deny:age", "age:senior", min_years=10))
    assert project(firm, dog_size="small", dog_age_years=12.0).state == "incompatible"


def test_cat_only_facility_blocks_a_dog():
    """`고양이 전용` 은 개를 데려가면 확실히 못 간다 — 판정 재료가 항상 있다."""
    facts = _one(_chip("deny:species_dog"))
    result = project(facts, dog_size="small", dog_age_years=3.0)

    assert result.state == "incompatible"
    assert result.reason == "species_denied"
