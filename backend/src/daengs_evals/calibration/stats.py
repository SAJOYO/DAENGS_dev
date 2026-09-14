"""숫자에 오차막대를 붙인다. 표준 라이브러리만 쓴다.

wilson_interval        비율의 신뢰구간. 소표본 · 0% · 100% 에서 정규근사보다 정직하다
bootstrap_difference   두 비율 차이의 CI. **질문 단위로 리샘플** — 같은 질문의 arm 들은 독립이 아니다
mcnemar_exact          짝지은 이진 결과의 검정. 이항 정확검정
sample_size_two_props  검정력 계산 — 문항 수를 결과 보고 정하지 않기 위해 먼저 돈다
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import NormalDist


@dataclass(frozen=True)
class Interval:
    point: float
    low: float
    high: float
    n: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "point": round(self.point, 4),
            "low": round(self.low, 4),
            "high": round(self.high, 4),
            "n": self.n,
        }


def wilson_interval(successes: int, n: int, *, confidence: float = 0.95) -> Interval:
    if n <= 0:
        return Interval(float("nan"), float("nan"), float("nan"), 0)
    z = NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    # 0/n · n/n 에서 centre ± half 가 부동소수점 오차로 0 · 1 을 1e-17 만큼 비껴간다 — 눈금 밖은 눈금으로
    low = round(max(0.0, centre - half), 12)
    high = round(min(1.0, centre + half), 12)
    return Interval(p, low, high, n)


def bootstrap_difference(
    a: Sequence[int],
    b: Sequence[int],
    *,
    resamples: int = 2_000,
    seed: int = 20260909,
    confidence: float = 0.95,
    statistic: Callable[[Sequence[int]], float] | None = None,
) -> Interval:
    """`mean(b) - mean(a)` 의 부트스트랩 CI. a · b 는 각 조건의 문항별 0/1 이고 서로 독립으로 리샘플한다.

    같은 문항이 두 조건에 다 있는 짝지은 자료면 `bootstrap_paired_difference` 를 쓴다.
    """
    stat = statistic or (lambda xs: sum(xs) / len(xs) if xs else float("nan"))
    if not a or not b:
        return Interval(float("nan"), float("nan"), float("nan"), 0)
    rng = random.Random(seed)
    point = stat(b) - stat(a)
    diffs = []
    for _ in range(resamples):
        ra = [a[rng.randrange(len(a))] for _ in a]
        rb = [b[rng.randrange(len(b))] for _ in b]
        diffs.append(stat(rb) - stat(ra))
    diffs.sort()
    lo_i = int((1 - confidence) / 2 * resamples)
    hi_i = int((1 + confidence) / 2 * resamples) - 1
    return Interval(point, diffs[lo_i], diffs[hi_i], min(len(a), len(b)))


def bootstrap_paired_difference(
    pairs: Sequence[tuple[int, int]],
    *,
    resamples: int = 2_000,
    seed: int = 20260909,
    confidence: float = 0.95,
) -> Interval:
    """문항마다 (조건 a 값, 조건 b 값). **문항 단위로** 리샘플해 `mean(b) - mean(a)` 의 CI 를 낸다."""
    if not pairs:
        return Interval(float("nan"), float("nan"), float("nan"), 0)
    rng = random.Random(seed)
    n = len(pairs)
    point = sum(b - a for a, b in pairs) / n
    diffs = []
    for _ in range(resamples):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        diffs.append(sum(b - a for a, b in sample) / n)
    diffs.sort()
    lo_i = int((1 - confidence) / 2 * resamples)
    hi_i = int((1 + confidence) / 2 * resamples) - 1
    return Interval(point, diffs[lo_i], diffs[hi_i], n)


def mcnemar_exact(b: int, c: int) -> dict[str, float | int]:
    """불일치 칸 둘(b: a만 1, c: b만 1)의 이항 정확검정. 양측 p."""
    n = b + c
    if n == 0:
        return {"b": b, "c": c, "n_discordant": 0, "p_value": 1.0}
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return {"b": b, "c": c, "n_discordant": n, "p_value": min(1.0, 2 * tail)}


def sample_size_two_props(p1: float, p2: float, *, alpha: float = 0.05, power: float = 0.8) -> int:
    """두 비율 차이를 잡는 데 필요한 **한 군의** 표본 수 (정규근사, 양측)."""
    nd = NormalDist()
    z_a = nd.inv_cdf(1 - alpha / 2)
    z_b = nd.inv_cdf(power)
    p_bar = (p1 + p2) / 2
    num = (
        z_a * math.sqrt(2 * p_bar * (1 - p_bar)) + z_b * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    ) ** 2
    return math.ceil(num / (p1 - p2) ** 2)


__all__ = [
    "Interval",
    "bootstrap_difference",
    "bootstrap_paired_difference",
    "mcnemar_exact",
    "sample_size_two_props",
    "wilson_interval",
]
