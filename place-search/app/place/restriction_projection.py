"""데려가는 개에 맞춰 조건 칩을 거르고, **증명 가능한 것만** 판정한다.

[결정 #70](../../docs/decisions/2026-08-27-place-row-tags.md) §3 이 "넓게 적고 좁게
판정한다" 고 정했다. 판독표는 원문이 말한 것을 전부 술어로 적고, 그 술어 중 무엇을
`incompatible` 로 승격할지는 **여기가** 정한다.

## 두 가지 다른 일

    투영   `applies_to` 를 이 개에 대고 걸러 남길 칩을 정한다 (표시)
    판정   남은 것 중 "못 간다" 가 증명되는 것만 골라낸다 (평가)

둘을 섞으면 안 된다. `require:muzzle@size:large` 는 대형견에게 **표시**되지만
`incompatible` 이 아니다 — 입마개를 채우면 갈 수 있다.

## 무엇을 판정하고 무엇을 안 하나

판정한다 (원문이 "못 들어온다" 고 말했고 현재 재료로 대조 가능):

    deny:size@size:large   × 대형견        원문이 크기로 배제했다
    deny:species_dog                       원문이 개를 배제했다
    deny:age (문턱 있고 firm)  × 문턱 안    술어의 `params` 가 `3개월 이하`·`4개월 미만`·
                                          `10세 이상` 을 구분해 보존한다

`unknown` 으로 둔다 — 술어로 **보여주되 가능하다고 부르지 않는다**:

    partial · raw_only             판독표가 원문을 전부 담지 못했다
    certainty=soft 인 것 전부       원문이 단정하지 않았다 — "어려울 수 있음"·"신규예약 불가".
                                   이것을 "이용 불가" 로 올리면 원문보다 강한 결론이 된다
    deny:age (문턱 없음)            `노령견` 처럼 원문이 숫자를 안 밝힌 경우. 기본값을
                                   지어내 판정하지 않는다
    require:carrier · require:hold   초대형 케이지가 있을 수도 있고 시설이 말하는
                                     "케이지" 의 규격을 우리는 모른다. 34kg 이면
                                     사실상 불가일 때가 많지만 그건 추론이다
    deny:behavior · deny:health      이 개가 공격적인지 아픈지 우리는 모른다
    deny:breed                       열거된 견종 이름을 술어가 안 담는다 (`breed:named`)
    require:vaccination · admin:*    충족 여부를 요청에서 받지 않는다

`evaluate_dog_access` 가 `weight_boundary_unknown` 으로 경계값을 지어내지 않는 것과
같은 규율이다. 모르는 것을 불가로 바꾸면 갈 수 있는 곳이 조용히 사라진다.

`owner.can_carry_kg` 가 살아나면 `require:hold` 는 그때 판정 대상이 된다 — 재료가
생기기 전에 추론으로 메우지 않는다.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.place.contracts import DogSize, RestrictionChip, RestrictionFacts

# `applies_to` 중 개의 값(크기·나이)으로 대조할 수 있는 것. 여기 없는 대상은 **거르지
# 않는다** — 판단 재료가 없으면 칩을 남기는 쪽이 안전하다(보호자가 읽고 판단한다).
_SIZE_SUBJECTS: dict[str, frozenset[DogSize]] = {
    "size:large": frozenset({"large"}),
    "size:medium_up": frozenset({"medium", "large"}),
    "size:small": frozenset({"small"}),
}

MONTHS_PER_YEAR = 12.0

RestrictionState = Literal["compatible", "incompatible", "unknown"]
RestrictionReason = Literal[
    "size_denied",
    "species_denied",
    "age_denied",
    "no_blocking_condition",
    "missing_dog_size",
    "missing_dog_age",
    "restrictions_unknown",
    "restrictions_not_applicable",
    "unverified_source_match",
    "incomplete_restrictions",
    "unresolved_condition",
]


class DogRestrictionEvaluation(BaseModel):
    """조건 술어를 이 개에 대고 본 결과. `dog_access` 와 다른 축이다.

    `dog_access` 는 `pet_size_class`·`max_kg`(원천 축)를 보고, 이쪽은 **문장에서
    파생한 술어**를 본다. 같은 시설이 두 축에서 다른 답을 낼 수 있고, 그건 원천이
    두 곳에 다르게 적었다는 사실이므로 합치지 않는다.
    """

    state: RestrictionState
    reason: RestrictionReason
    # **이 개에게 보여줄 칩.** `facts.restrictions.chips` 는 장소 사실이라 전부 들고 있고,
    # 이쪽은 그중 해당하는 것만이다 — 소형견 요청에서 `#입마개·대형견` 이 여기서 사라진다.
    chips: list[RestrictionChip] = Field(default_factory=list)
    # 판정 근거가 된 술어 코드. 왜 못 가는지 화면이 설명할 수 있어야 한다.
    blocking: list[str] = Field(default_factory=list)


def age_threshold_years(chip: RestrictionChip) -> float | None:
    """이 나이 술어가 지키는 문턱(년). 원문이 숫자를 안 밝혔으면 `None`.

    `3개월 이하`·`4개월 미만`·`5개월 이상`·`10세 이상` 이 전부 다른 문장이다.
    하나로 뭉개면 5살 개가 "4개월 미만 입장 불가" 에 걸린다.
    """
    months = chip.params.get("max_months")
    if months is not None:
        return float(months) / MONTHS_PER_YEAR
    years = chip.params.get("min_years")
    return float(years) if years is not None else None


def _age_applies(chip: RestrictionChip, dog_age_years: float | None) -> bool | None:
    """이 개가 나이 문턱 안에 드는가. `None` 은 판단 불가(문턱 없음 또는 나이 모름)."""
    threshold = age_threshold_years(chip)
    if threshold is None or dog_age_years is None:
        return None
    if chip.applies_to == "age:puppy":
        return dog_age_years < threshold
    if chip.applies_to == "age:senior":
        return dog_age_years >= threshold
    return None


def applies_to_dog(
    chip: RestrictionChip,
    *,
    dog_size: DogSize | None,
    dog_age_years: float | None = None,
) -> bool:
    """이 칩을 **이 개에게 보여야 하는가.**

    모르면 `True` 다. 조건을 숨기는 실수가 보여주는 실수보다 나쁘다 — 보호자는
    본 것을 확인할 수 있지만 안 보인 것은 확인할 방법이 없다.
    """
    subject = chip.applies_to
    if subject == "all":
        return True
    if subject in _SIZE_SUBJECTS:
        if dog_size is None:
            return True
        return dog_size in _SIZE_SUBJECTS[subject]
    if subject in ("age:puppy", "age:senior"):
        # 문턱이 있고 나이를 알면 실제로 가른다. 둘 중 하나라도 없으면 남긴다 —
        # 숨기는 실수가 보여주는 실수보다 나쁘다.
        applies = _age_applies(chip, dog_age_years)
        return True if applies is None else applies
    # sex · 중성화 · 생리 · 견종: 프로필로 못 가른다. 남긴다.
    return True


def project(
    facts: RestrictionFacts | None,
    *,
    dog_size: DogSize | None,
    dog_age_years: float | None,
) -> DogRestrictionEvaluation:
    """이 개 기준의 칩과 판정. **원본 `facts` 는 건드리지 않는다.**

    장소 사실(`facts.restrictions`)과 요청별 파생(`evaluations`)을 가르는 것은
    결정 #68 의 규율 그대로다 — 같은 시설도 데려가는 개에 따라 답이 달라진다.
    """
    if facts is None or facts.state == "unknown":
        return DogRestrictionEvaluation(
            state="unknown", reason="restrictions_unknown",
        )
    if facts.state == "none_confirmed":
        return DogRestrictionEvaluation(
            state="compatible", reason="no_blocking_condition",
        )
    if facts.state == "not_applicable":
        # 이 축에 적용할 조건이 없다는 뜻이지, 개가 들어갈 수 있다는 뜻이 아니다.
        # 실제 동반 불가는 별도 dog_access 축이 말한다.
        return DogRestrictionEvaluation(
            state="unknown", reason="restrictions_not_applicable",
        )

    visible = [
        chip for chip in facts.chips
        if applies_to_dog(chip, dog_size=dog_size, dog_age_years=dog_age_years)
    ]

    for chip in visible:
        # **약한 술어는 어느 코드든 판정하지 않는다.** 원문이 단정하지 않았다.
        if chip.certainty == "soft":
            continue
        if chip.code == "deny:size":
            if dog_size is None:
                return DogRestrictionEvaluation(
                    state="unknown", reason="missing_dog_size",
                    chips=visible, blocking=[chip.code],
                )
            return DogRestrictionEvaluation(
                state="incompatible", reason="size_denied",
                chips=visible, blocking=[chip.code],
            )
        if chip.code == "deny:species_dog":
            return DogRestrictionEvaluation(
                state="incompatible", reason="species_denied",
                chips=visible, blocking=[chip.code],
            )
        if chip.code == "deny:age" and age_threshold_years(chip) is not None:
            # 여기 온 칩은 `applies_to_dog` 를 통과했다 — 나이를 모르면 통과했을
            # 뿐이므로 판정은 못 한다.
            if dog_age_years is None:
                return DogRestrictionEvaluation(
                    state="unknown", reason="missing_dog_age",
                    chips=visible, blocking=[chip.code],
                )
            return DogRestrictionEvaluation(
                state="incompatible", reason="age_denied",
                chips=visible, blocking=[chip.code],
            )

    # 판독이 불완전하면 현재 칩에 blocker가 없다는 사실로 원문 전체가 안전하다고
    # 결론내릴 수 없다. 위의 확정 blocker만 먼저 판정하고 나머지는 fail closed 한다.
    if facts.parse_state in ("partial", "raw_only"):
        return DogRestrictionEvaluation(
            state="unknown", reason="incomplete_restrictions", chips=visible,
        )

    # 요구사항·연령·건강·견종 등은 표시할 수 있지만 현재 요청에는 충족 여부가 없다.
    # `compatible` 은 "불가를 못 찾음" 이 아니라 "남은 미해결 조건이 없음" 일 때만 쓴다.
    unresolved = [chip for chip in visible if chip.code != "deny:species_cat"]
    if unresolved or not facts.chips:
        return DogRestrictionEvaluation(
            state="unknown", reason="unresolved_condition", chips=visible,
        )

    return DogRestrictionEvaluation(
        state="compatible", reason="no_blocking_condition", chips=visible,
    )
