"""사람과 판정기가 얼마나 맞나 — **우연을 빼고**.

단순 일치율은 한쪽으로 쏠린 데이터에서 부풀려진다. `agreement_life_v1.md`(#348) 의 30건은 24건이
(사람 2, judge 2) 라 아무렇게나 찍어도 73% 가 맞고, 단순 일치율 0.87 / 0.90 이 κ 로는 0.50 / 0.67
이다. 이 모듈이 그 계산이다.

    cohen_kappa           범주형 (이진 항목 — fabricated · stereotype · changed)
    weighted_kappa        순서형 (responsiveness 0~2), 선형 가중
    krippendorff_alpha    결측 · 기권이 섞인 자료. nominal / ordinal

**"못 잼" 과 "미달" 은 다르다.** 한 범주만 관측되면 κ 는 정의되지 않는다(0/0). 그걸 0.0 으로 적으면
"판정기가 사람과 안 맞는다" 로 읽히는데 사실은 **잴 수가 없었던** 것이다. 그래서 값 대신 이유를
돌려준다 — `gate.py` 가 그 둘을 다른 사유 코드로 가른다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

Undefined = Literal["too_few", "single_category", "no_variation"]


@dataclass(frozen=True)
class Agreement:
    value: float | None
    n: int
    observed: float | None
    expected: float | None
    undefined: Undefined | None = None
    #: 사람 · 판정기 각각의 주변분포 — 쏠림을 리포트가 보게
    marginals: dict[str, dict[str, int]] | None = None

    @property
    def measurable(self) -> bool:
        return self.value is not None


def _paired(a: Sequence[Any], b: Sequence[Any]) -> list[tuple[Any, Any]]:
    if len(a) != len(b):
        raise ValueError(f"길이가 다르다: {len(a)} vs {len(b)}")
    return [(x, y) for x, y in zip(a, b, strict=True) if x is not None and y is not None]


def cohen_kappa(human: Sequence[Any], judge: Sequence[Any], *, min_n: int = 2) -> Agreement:
    pairs = _paired(human, judge)
    n = len(pairs)
    if n < min_n:
        return Agreement(None, n, None, None, "too_few")
    hm = Counter(h for h, _ in pairs)
    jm = Counter(j for _, j in pairs)
    marginals = {"human": dict(hm), "judge": dict(jm)}
    categories = set(hm) | set(jm)
    po = sum(1 for h, j in pairs if h == j) / n
    pe = sum((hm[c] / n) * (jm[c] / n) for c in categories)
    if len(categories) < 2:
        return Agreement(None, n, po, pe, "single_category", marginals)
    if pe >= 1.0:
        return Agreement(None, n, po, pe, "no_variation", marginals)
    return Agreement((po - pe) / (1 - pe), n, po, pe, None, marginals)


def weighted_kappa(
    human: Sequence[int | None],
    judge: Sequence[int | None],
    *,
    levels: Sequence[int],
    min_n: int = 2,
) -> Agreement:
    """선형 가중 κ. 한 칸 어긋난 것은 두 칸 어긋난 것보다 덜 틀린 것이다."""
    pairs = _paired(human, judge)
    n = len(pairs)
    if n < min_n:
        return Agreement(None, n, None, None, "too_few")
    lv = list(levels)
    k = len(lv)
    idx = {v: i for i, v in enumerate(lv)}
    hm = Counter(h for h, _ in pairs)
    jm = Counter(j for _, j in pairs)
    marginals = {
        "human": {str(a): b for a, b in hm.items()},
        "judge": {str(a): b for a, b in jm.items()},
    }
    if len(set(hm) | set(jm)) < 2:
        return Agreement(None, n, None, None, "single_category", marginals)

    def w(i: int, j: int) -> float:
        return abs(i - j) / (k - 1) if k > 1 else 0.0

    observed_disagreement = sum(w(idx[h], idx[j]) for h, j in pairs) / n
    expected_disagreement = sum(
        w(idx[a], idx[b]) * (hm[a] / n) * (jm[b] / n) for a in hm for b in jm
    )
    if expected_disagreement == 0:
        return Agreement(
            None, n, 1 - observed_disagreement, 1 - expected_disagreement, "no_variation", marginals
        )
    value = 1 - observed_disagreement / expected_disagreement
    return Agreement(
        value, n, 1 - observed_disagreement, 1 - expected_disagreement, None, marginals
    )


def krippendorff_alpha(
    ratings: Sequence[Sequence[Any]],
    *,
    metric: Literal["nominal", "ordinal"] = "nominal",
    min_units: int = 2,
) -> Agreement:
    """단위마다 여러 평가자의 값. 결측은 `None`. 두 명 이상 매긴 단위만 센다.

    사람 라벨 + 판정기 + 부판정기처럼 평가자가 셋이거나, 기권으로 빈 칸이 있을 때 κ 대신 쓴다.
    """
    units = [[v for v in unit if v is not None] for unit in ratings]
    units = [u for u in units if len(u) >= 2]
    if len(units) < min_units:
        return Agreement(None, len(units), None, None, "too_few")
    values = sorted({v for u in units for v in u})
    if len(values) < 2:
        return Agreement(None, len(units), None, None, "single_category")
    rank = {v: i for i, v in enumerate(values)}
    total = Counter(v for u in units for v in u)
    n_total = sum(total.values())

    def delta(a: Any, b: Any) -> float:
        if metric == "nominal":
            return 0.0 if a == b else 1.0
        ra, rb = rank[a], rank[b]
        lo, hi = min(ra, rb), max(ra, rb)
        # 순서형: 두 값 사이(양끝 절반 포함)에 놓인 관측 빈도의 합
        between = (
            sum(total[values[i]] for i in range(lo, hi + 1))
            - (total[values[lo]] + total[values[hi]]) / 2
        )
        return between**2

    do = 0.0
    for u in units:
        m = len(u)
        do += sum(delta(a, b) for i, a in enumerate(u) for b in u[i + 1 :]) * 2 / (m - 1)
    do /= n_total
    de = sum(delta(a, b) * total[a] * total[b] for a in values for b in values if a != b) / (
        n_total * (n_total - 1)
    )
    if de == 0:
        return Agreement(None, len(units), None, None, "no_variation")
    return Agreement(1 - do / de, len(units), None, None, None)


def simple_agreement(human: Sequence[Any], judge: Sequence[Any]) -> float | None:
    """참고용 단순 일치율. 리포트는 κ 옆에만 둔다 — 혼자 쓰면 부풀려진다."""
    pairs = _paired(human, judge)
    return sum(1 for h, j in pairs if h == j) / len(pairs) if pairs else None


__all__ = [
    "Agreement",
    "cohen_kappa",
    "krippendorff_alpha",
    "simple_agreement",
    "weighted_kappa",
]
