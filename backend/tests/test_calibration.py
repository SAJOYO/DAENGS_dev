"""판정기를 믿을 근거 — 위생 · 일치도 · 통계 · 게이트. 모델 없이 돈다."""

from __future__ import annotations

import pytest

from daengs_evals.calibration.agreement import (
    cohen_kappa,
    krippendorff_alpha,
    simple_agreement,
    weighted_kappa,
)
from daengs_evals.calibration.gate import Thresholds, require_calibration, withhold
from daengs_evals.calibration.hygiene import (
    JudgeHygieneError,
    model_family,
    require_family_split,
    require_judge_hygiene,
    require_pinned_model,
)
from daengs_evals.calibration.stats import (
    bootstrap_paired_difference,
    mcnemar_exact,
    sample_size_two_props,
    wilson_interval,
)

# ---------------------------------------------------------------------------
# 위생
# ---------------------------------------------------------------------------


def test_family_split_rejects_gemini_judging_gemini() -> None:
    """#348 이 gemini-3.1-pro-preview 로 채점한 구성 — 규칙상 안 된다."""
    with pytest.raises(JudgeHygieneError, match="같은 계열"):
        require_family_split("gemini-3.1-pro-preview", ["gemini-3.1-flash-lite"])


def test_family_split_accepts_openai_over_gemini() -> None:
    assert require_family_split("gpt-5.4-2026-03-05", ["gemini-3.1-flash-lite"]) == "openai"


def test_pinned_model_rejects_moving_names() -> None:
    for name in ("gpt-5-latest", "gemini-3.1-pro-preview", "gpt-5.4"):
        with pytest.raises(JudgeHygieneError):
            require_pinned_model(name)
    assert require_pinned_model("gpt-5.4-2026-03-05") == "gpt-5.4-2026-03-05"


def test_unknown_family_is_not_silently_accepted() -> None:
    with pytest.raises(JudgeHygieneError, match="계열을 모르는"):
        model_family("mystery-9000")


def test_hygiene_bundle_reports_both_families() -> None:
    out = require_judge_hygiene("gpt-5.4-2026-03-05", ["gemini-3.1-flash-lite"])
    assert out == {
        "judge_model": "gpt-5.4-2026-03-05",
        "judge_family": "openai",
        "generation_families": "google",
    }


# ---------------------------------------------------------------------------
# 일치도 — #348 의 표를 손계산과 대조한다
# ---------------------------------------------------------------------------


def _expand(cells: dict[tuple[int, int], int]) -> tuple[list[int], list[int]]:
    human, judge = [], []
    for (h, j), n in cells.items():
        human += [h] * n
        judge += [j] * n
    return human, judge


def test_kappa_matches_the_hand_check_of_agreement_life_v1() -> None:
    """agreement_life_v1.md 의 variant A: 단순 일치 0.87 인데 κ 는 0.50 이다."""
    human, judge = _expand({(1, 0): 1, (1, 1): 2, (1, 2): 3, (2, 2): 24})
    k = cohen_kappa(human, judge)
    assert k.n == 30
    assert round(k.observed or 0, 3) == 0.867
    assert round(k.expected or 0, 3) == 0.733
    assert round(k.value or 0, 2) == 0.50
    assert round(simple_agreement(human, judge) or 0, 2) == 0.87

    human_b, judge_b = _expand({(1, 1): 4, (1, 2): 2, (2, 1): 1, (2, 2): 23})
    assert round(cohen_kappa(human_b, judge_b).value or 0, 2) == 0.67


def test_kappa_is_one_on_perfect_agreement_and_negative_on_systematic_disagreement() -> None:
    assert cohen_kappa([0, 1, 0, 1], [0, 1, 0, 1]).value == pytest.approx(1.0)
    assert (cohen_kappa([0, 1, 0, 1], [1, 0, 1, 0]).value or 0) < 0


def test_single_category_is_undefined_not_zero() -> None:
    """전부 같은 값이면 κ 는 0/0 이다. 0.0 으로 적으면 '안 맞는다' 로 읽힌다."""
    k = cohen_kappa([1, 1, 1, 1], [1, 1, 1, 1])
    assert k.value is None
    assert k.undefined == "single_category"
    assert k.observed == 1.0


def test_kappa_ignores_missing_labels() -> None:
    k = cohen_kappa([1, None, 0, 1], [1, 1, 0, None])
    assert k.n == 2


def test_weighted_kappa_penalises_far_misses_more() -> None:
    near = weighted_kappa([0, 1, 2, 0, 1, 2], [0, 1, 1, 0, 2, 2], levels=[0, 1, 2])
    far = weighted_kappa([0, 1, 2, 0, 1, 2], [2, 1, 0, 0, 1, 2], levels=[0, 1, 2])
    assert (near.value or 0) > (far.value or 0)


def test_krippendorff_alpha_handles_missing_and_three_raters() -> None:
    ratings = [[1, 1, 1], [0, 0, None], [1, 1, 1], [0, 0, 0], [1, None, 1], [0, 1, 0]]
    a = krippendorff_alpha(ratings)
    assert a.measurable and 0 < (a.value or 0) <= 1
    assert krippendorff_alpha([[1, 1], [1, 1]]).undefined == "single_category"


# ---------------------------------------------------------------------------
# 통계
# ---------------------------------------------------------------------------


def test_wilson_interval_is_honest_at_the_edges() -> None:
    zero = wilson_interval(0, 10)
    assert zero.point == 0.0 and zero.low == 0.0 and zero.high > 0.0
    full = wilson_interval(10, 10)
    assert full.point == 1.0 and full.high == 1.0 and full.low < 1.0
    mid = wilson_interval(18, 36)
    assert mid.low < 0.5 < mid.high


def test_bootstrap_paired_difference_is_deterministic_and_brackets_the_point() -> None:
    pairs = [(0, 1)] * 10 + [(0, 0)] * 5 + [(1, 1)] * 3
    a = bootstrap_paired_difference(pairs, resamples=500)
    b = bootstrap_paired_difference(pairs, resamples=500)
    assert a == b
    assert a.low <= a.point <= a.high
    assert a.point == pytest.approx(10 / 18)


def test_mcnemar_exact_known_values() -> None:
    assert mcnemar_exact(0, 0)["p_value"] == 1.0
    assert mcnemar_exact(5, 5)["p_value"] == 1.0
    assert mcnemar_exact(10, 0)["p_value"] == pytest.approx(2 / 1024)


def test_sample_size_justifies_36_questions() -> None:
    """변화율 0.2 vs 0.6 을 80% 검정력으로 잡으려면 군당 몇 문항인가 — 계획의 근거."""
    n = sample_size_two_props(0.2, 0.6)
    assert 20 <= n <= 30


# ---------------------------------------------------------------------------
# 게이트
# ---------------------------------------------------------------------------


def test_gate_distinguishes_below_from_cannot_measure() -> None:
    human, judge = _expand({(1, 0): 1, (1, 1): 2, (1, 2): 3, (2, 2): 24})
    low = cohen_kappa(human, judge)  # 0.50, n=30, 사람 주변 {1: 6, 2: 24}
    few = cohen_kappa([1, 0, 1], [1, 0, 0])
    single = cohen_kappa([1] * 30, [1] * 30)
    report = require_calibration({"a": low, "b": few, "c": single, "d": None})
    assert report.withheld == {
        "a": "below_threshold",
        "b": "too_few_labels",
        "c": "undefined",  # 30건이 전부 같은 값 — 표본이 적은 게 아니라 κ 가 정의되지 않는다
        "d": "not_calibrated",
    }


def test_gate_flags_sparse_marginal_even_when_kappa_is_high() -> None:
    """29건이 1 이고 1건만 0 인데 그 한 건을 맞혔다 — κ 는 1.0 이지만 믿을 근거가 없다."""
    human = [1] * 29 + [0]
    k = cohen_kappa(human, human)
    d = require_calibration({"x": k}, Thresholds(min_marginal=3)).decisions["x"]
    assert not d.passed and d.reason == "sparse_marginal"


def test_gate_passes_a_good_item() -> None:
    human = [0, 1] * 20
    judge = human[:-2] + [1, 0]
    d = require_calibration({"x": cohen_kappa(human, judge)}).decisions["x"]
    assert d.passed and d.reason is None


def test_withhold_blanks_numbers_and_records_reasons() -> None:
    human = [1] * 20 + [0] * 10
    judge = [1] * 15 + [0] * 5 + [1] * 5 + [0] * 5  # κ 낮음
    gate = require_calibration({"fab": cohen_kappa(human, judge), "resp": None})
    summary = {"items": {"fab": 0.91, "resp": 1.7, "inv": 0.8}, "n": 30}
    out = withhold(summary, gate)
    assert out["items"]["fab"] is None and out["items"]["resp"] is None
    assert out["items"]["inv"] == 0.8  # 게이트가 말하지 않은 항목은 손대지 않는다
    assert set(out["unmeasured"]) == {"fab", "resp"}
    assert summary["items"]["fab"] == 0.91  # 원본 불변
