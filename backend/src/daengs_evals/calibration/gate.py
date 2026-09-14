"""κ 미달이면 숫자를 안 낸다 — 두 겹.

① `require_calibration` 은 러너가 요약을 만들기 전에 부른다. 통과 못 한 항목은 요약에 숫자가
   안 들어간다.
② `withhold` 는 리포트가 렌더 직전에 부른다. 이미 만들어진 요약에서 그 항목의 숫자를 지우고 사유를
   남긴다. 러너를 우회해 리포트만 다시 그려도 숫자가 살아나지 않게.

**사유 코드는 셋으로 가른다.** "미달"(κ 가 낮다) 과 "못 잼"(표본이 적다 · 한쪽으로 쏠렸다) 은 고치는
방법이 다르다. 하나로 뭉치면 누군가 임계값을 낮춰서 "고치게" 된다. 임계값은 `benchmark_pf_v1.yaml`
에 먼저 적고 결과를 본다 — 여기 기본값은 그 파일이 없을 때의 안전한 쪽이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from daengs_evals.calibration.agreement import Agreement

WithheldReason = Literal[
    "below_threshold", "too_few_labels", "sparse_marginal", "undefined", "not_calibrated"
]


@dataclass(frozen=True)
class Thresholds:
    kappa_min: float = 0.6
    min_labels: int = 30
    #: 어느 한 범주의 사람 라벨이 이보다 적으면 κ 가 불안정하다 (`no_fabrication` 이 95/5 로 쏠리는 자리)
    min_marginal: int = 3


@dataclass(frozen=True)
class Decision:
    item: str
    passed: bool
    reason: WithheldReason | None
    kappa: float | None
    n: int
    detail: str = ""


@dataclass
class GateReport:
    decisions: dict[str, Decision] = field(default_factory=dict)

    @property
    def passed_items(self) -> list[str]:
        return [k for k, d in self.decisions.items() if d.passed]

    @property
    def withheld(self) -> dict[str, WithheldReason]:
        return {
            k: d.reason for k, d in self.decisions.items() if not d.passed and d.reason is not None
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            k: {
                "passed": d.passed,
                "reason": d.reason,
                "kappa": d.kappa,
                "n": d.n,
                "detail": d.detail,
            }
            for k, d in self.decisions.items()
        }


#: `benchmark_pf_v1.yaml` 이 없을 때의 기본. 결과를 보고 여기를 낮추지 않는다.
DEFAULT_THRESHOLDS = Thresholds()


def decide(
    item: str, agreement: Agreement | None, thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> Decision:
    if agreement is None:
        return Decision(item, False, "not_calibrated", None, 0, "사람 라벨이 없다")
    if agreement.n < thresholds.min_labels:
        return Decision(
            item,
            False,
            "too_few_labels",
            agreement.value,
            agreement.n,
            f"라벨 {agreement.n}건 < {thresholds.min_labels}",
        )
    if agreement.undefined is not None:
        return Decision(
            item, False, "undefined", None, agreement.n, f"κ 정의 안 됨: {agreement.undefined}"
        )
    if agreement.marginals:
        human = agreement.marginals.get("human", {})
        thin = {k: v for k, v in human.items() if v < thresholds.min_marginal}
        if thin:
            return Decision(
                item,
                False,
                "sparse_marginal",
                agreement.value,
                agreement.n,
                f"사람 라벨이 쏠렸다 — 범주별 {human} (기준 {thresholds.min_marginal})",
            )
    assert agreement.value is not None
    if agreement.value < thresholds.kappa_min:
        return Decision(
            item,
            False,
            "below_threshold",
            agreement.value,
            agreement.n,
            f"κ {agreement.value:.2f} < {thresholds.kappa_min}",
        )
    return Decision(item, True, None, agreement.value, agreement.n)


def require_calibration(
    agreements: Mapping[str, Agreement | None], thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> GateReport:
    report = GateReport()
    for item, agreement in agreements.items():
        report.decisions[item] = decide(item, agreement, thresholds)
    return report


def withhold(
    summary: Mapping[str, Any], gate: GateReport, *, items_key: str = "items"
) -> dict[str, Any]:
    """통과 못 한 항목의 숫자를 요약에서 지운다. 지운 자리는 `None`, 사유는 `unmeasured` 에."""
    out: dict[str, Any] = dict(summary)
    items = dict(out.get(items_key) or {})
    notes: dict[str, str] = dict(out.get("unmeasured") or {})
    for item, reason in gate.withheld.items():
        if item in items:
            items[item] = None
        d = gate.decisions[item]
        notes[item] = f"{reason}: {d.detail}".strip(": ")
    out[items_key] = items
    out["unmeasured"] = notes
    out["gate"] = gate.as_dict()
    return out


__all__ = ["Decision", "GateReport", "Thresholds", "decide", "require_calibration", "withhold"]
