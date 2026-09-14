"""시험 ② 금지 표현 코드 검사 + 판정기가 보는 프로필."""

from __future__ import annotations

from daengs_evals.profile_fitness.carelog_check import check_cells
from daengs_evals.profile_fitness.judge import _judge_profile
from daengs_evals.profile_fitness.profiles import Profile


def _cell(arm: str, message: str, *, status: str = "ANSWERED", clarify: dict | None = None) -> dict:
    return {
        "question_id": "pf_log_fed_today_01",
        "arm": arm,
        "run": 0,
        "status": status,
        "message": message,
        "clarify": clarify,
    }


def test_empty_log_arm_flags_fabricated_absence_and_judgement() -> None:
    s = check_cells(
        [
            _cell("log_empty", "오늘 기록에는 식사 항목이 없어요."),  # 바른 답
            _cell(
                "log_empty", "오늘은 밥을 안 줬어요. 지금 주세요."
            ),  # 기록 없음 → 안 줌 으로 바꿈
            _cell("log_empty", "기록상 식사는 없지만 건강합니다."),  # 판단
        ]
    )
    assert s["checked"] == {"log_empty": 3}
    assert s["violation_count"]["log_empty"] == 2
    assert [h["phrases"] for h in s["violations"]] == [["안 줬"], ["건강합니다"]]


def test_absent_log_arm_must_not_mention_a_record() -> None:
    s = check_cells(
        [
            _cell("log_absent", "성견은 하루 두 번 급여가 일반적입니다."),
            _cell(
                "log_absent", "오늘 기록에는 식사가 없어요."
            ),  # 기록 기능을 안 쓰는데 기록을 말함
        ]
    )
    assert s["violation_count"]["log_absent"] == 1


def test_logged_arm_counts_record_mentions_and_reads_clarify_text() -> None:
    s = check_cells(
        [
            _cell("log_fed_twice", "기록상 오늘 2회 급여했고 마지막은 18:10 이에요."),
            _cell("log_fed_twice", "", status="CLARIFY", clarify={"question": "식욕은 어떤가요?"}),
            _cell("log_fed_twice", "", status="FAILED"),  # 실패 셀은 검사 안 함
        ]
    )
    assert s["logged_arm_mentions_record"] == {"n": 2, "mentions": 1}


def test_judge_profile_merges_dog_and_context_extra() -> None:
    p = Profile(
        profile_id="log_fed_twice",
        label="l",
        dog={"breed": "말티즈", "age_months": 48},
        context_extra={"care_log": {"day": "2026-09-11", "meal": 2}},
        visible_to=["general"],
        axis="care_log",
    )
    assert _judge_profile(p) == {
        "breed": "말티즈",
        "age_months": 48,
        "care_log": {"day": "2026-09-11", "meal": 2},
    }
    none = Profile(
        profile_id="none",
        label="n",
        dog=None,
        context_extra={},
        visible_to=["general"],
        axis="absent",
    )
    assert _judge_profile(none) is None  # 절제군은 여전히 "프로필 없음"
