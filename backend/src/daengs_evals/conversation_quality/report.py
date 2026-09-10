"""축별 리포트와 전후 비교 (#401).

**모델을 부르지 않는다.** 이 모듈은 이미 있는 랩 파일 · 판정 파일에서 결정론으로 표를
만든다 — `judge.py` 를 다시 부르지 않으므로 여기서 나는 숫자는 늘 재현된다
(`test_report_is_deterministic`).

────────────────────────────────────────────────────────────────────────────
숫자의 지위
────────────────────────────────────────────────────────────────────────────
세 축 전부 `not_calibrated` 다 (D-060 ⑦). 사람 라벨 없이는 "판정기가 맞았다"를 확인할
길이 없어서, 이 리포트가 하는 일은 채점이 아니라 **사람이 볼 자리를 고르는 것**이다.
그래서 표에는 합계도 종합 점수도 없다 — `rubric.AxisScores` 에 `total` 칸이 없는 것과
같은 이유다 (안전 실패가 말투 점수에 묻힌다).

이 카드에는 **안전 축이 없다.** `rubric.derive_usability` 가 받는 `safety_failed` 를 이
모듈은 늘 `False` 로 부른다 — 그것은 "안전을 통과했다"는 뜻이 아니라 **이 카드가 안전을
아예 재지 않는다**는 뜻이다. 사용성 게이트 표의 `safety` 칸이 0 인 것을 "안전 문제 없음"
으로 읽지 마라.

────────────────────────────────────────────────────────────────────────────
미측정 비율 — 카드가 요구하는 여섯째 고정값
────────────────────────────────────────────────────────────────────────────
분모는 **랩의 턴 수 × 3 축**이다(턴 하나가 축 셋을 채울 수 있는 자리다). 분자는 다음
셋의 합이다 — 전부 랩 · 판정 파일이 이미 들고 있는 값에서만 나온다, 다시 판정하지 않는다:

  판정 전 제외      `JudgeHeader.skipped` × 3 — 빈 답변 · `NOT_REACHED` 라 판정기를
                  아예 안 불렀다(D-060 ⑤). 축 셋 전부가 미측정이다.
  가짜 어댑터 셀    `TurnSnapshot.answered_by_fake_adapter is True` 인 행은 판정됐어도
                  뺀다 — 진짜 응답이 아니라 자리표시자를 채점한 것이다. 다른 브랜치의
                  평가가 이것을 안 갈라 14% 대 5% 로 갈린 적이 있다(`collect.py` 문서).
                  가짜인지 모르는 행(`NOT_REACHED`)은 배제하지 않는다 — 판정 자체가
                  이미 그 행을 걸렀거나(빈 답변), 판단할 근거가 없다는 뜻이라 있는 그대로
                  둔다.
  해당 없는 축      `TurnJudgment.not_applicable` — 잴 수 없어서 안 부른 축.

**적용 안 됨(0 이 아니다) 과 미측정을 섞지 않는다.** 이 셋 중 무엇에도 안 걸리는
축-자리만 "측정됨"으로 표의 평균·분포에 들어간다.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from daengs_evals.conversation_quality.judge import AXES, TurnJudgment
from daengs_evals.conversation_quality.rubric import derive_usability

CARD = "#401"

#: 랩 두 개를 견주려면 이 다섯이 안 움직여야 한다 (카드가 요구한 여섯 중 다섯 —
#: 여섯째인 "미측정 비율 정의"는 값이 아니라 이 모듈의 계산 방법이라 여기 안 낀다).
PINNED_FIELDS: tuple[str, ...] = (
    "cases_sha256",
    "judge_model",
    "prompt_version",
    "anchor_set",
    "adapter_mode",
)

#: 오늘 정답이 0 으로 고정된 두 축 (`transcript.PRIOR_TURNS_REACH_INFERENCE is False`).
#: 이 둘의 before → after 는 "모델이 좋아졌다"가 아니라 "기능이 생겼다"다.
FLOORED_AXES: tuple[str, ...] = ("context_continuity", "repair_success")

_FEATURE_ABSENT = "기능 부재"


# ---------------------------------------------------------------------------
# 요약 모양
# ---------------------------------------------------------------------------


class AxisStat(BaseModel):
    """축 하나의 평균 · 분포 · n. **합계가 없다** — 세 축을 합칠 칸 자체를 안 둔다."""

    model_config = ConfigDict(extra="forbid")

    n: int
    mean: float | None
    #: 점수(0·1·2) → 그 점수를 받은 턴 수.
    distribution: dict[int, int]


class UsabilityTally(BaseModel):
    """사용성 게이트 집계. `safety` 칸은 이 카드가 안전을 재지 않아 늘 0 이다 — 위 모듈
    docstring 참고."""

    model_config = ConfigDict(extra="forbid")

    usable: int
    unusable_safety: int
    unusable_response_mode_fit: int
    unusable_repair_success: int


class StateAuditTally(BaseModel):
    """상태 감사 네 칸의 **사실 집계**. 점수가 아니다 — `rubric.StateAudit` 과 같은 자리."""

    model_config = ConfigDict(extra="forbid")

    n_audited: int
    relevant_state_available: int
    relevant_state_used: int
    state_used_correctly: int
    unsupported_or_superficial_personalization: int


class UnmeasuredTally(BaseModel):
    """분자 = 판정 전 제외 + 가짜 어댑터 셀 + 해당 없는 축. 분모 = 턴 수 × 3."""

    model_config = ConfigDict(extra="forbid")

    numerator: int
    denominator: int
    excluded_before_judging_slots: int
    fake_adapter_slots: int
    not_applicable_slots: int

    @property
    def ratio(self) -> float | None:
        return round(self.numerator / self.denominator, 4) if self.denominator else None


class Summary(BaseModel):
    """리포트 한 장이 담는 것 전부. **합계 칸이 없다.**"""

    model_config = ConfigDict(extra="forbid")

    lap: str
    cases_sha256: str
    judge_model: str
    prompt_version: int
    anchor_set: str
    adapter_mode: str
    n_turns_total: int
    n_turns_judged: int
    axis_stats: dict[str, AxisStat]
    usability: UsabilityTally
    state_audit: StateAuditTally
    unmeasured: UnmeasuredTally
    #: 판정 축이 아니라 파생 진단이다 — `response_mode_fit == 0` 인 턴 수 위에서만 잰다.
    #: 병원 안내처럼 형식상 다음 행동이 있는 막다른 길은 이 수에 안 잡힌다 (`rubric.py` 참고).
    dead_end_count: int
    dead_end_n: int
    calibration: Literal["not_calibrated"] = "not_calibrated"


# ---------------------------------------------------------------------------
# 적재 — 랩 · 판정 파일 그대로
# ---------------------------------------------------------------------------


def load_judgments(path: Path) -> tuple[dict[str, Any], list[TurnJudgment]]:
    """판정 파일(헤더 한 줄 + 판정 줄들)을 읽는다. `collect.load_lap` 과 같은 모양."""
    lines = [line for line in path.read_text("utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"{path} 가 비어 있습니다")
    header = json.loads(lines[0])
    judgments = [TurnJudgment.model_validate(json.loads(line)) for line in lines[1:]]
    return header, judgments


# ---------------------------------------------------------------------------
# 요약 — 이미 있는 값만 센다, 다시 판정하지 않는다
# ---------------------------------------------------------------------------


def summarize(
    *,
    lap_meta: Mapping[str, Any],
    lap_rows: Sequence[Mapping[str, Any]],
    judge_header: Mapping[str, Any],
    judgments: Sequence[TurnJudgment],
) -> Summary:
    """랩 헤더·행 + 판정 헤더·판정 목록 → `Summary`. **여기서 판정기를 부르지 않는다.**"""
    fake_keys = {
        (str(row.get("case_id")), int(row.get("turn_index", -1)))
        for row in lap_rows
        if row.get("answered_by_fake_adapter") is True
    }

    axis_values: dict[str, list[int]] = {axis: [] for axis in AXES}
    usable = unusable_safety = unusable_rmf = unusable_repair = 0
    state_n = state_avail = state_used = state_correct = state_bad = 0
    not_applicable_slots = 0
    fake_adapter_slots = 0
    dead_end_count = 0
    dead_end_n = 0

    for judgment in judgments:
        key = (judgment.case_id, judgment.turn_index)
        if key in fake_keys:
            # 판정은 됐지만 답한 것은 자리표시자다 — 축 셋 전부를 미측정으로 뺀다.
            fake_adapter_slots += len(AXES)
            continue

        not_applicable_slots += len(judgment.not_applicable)
        for axis in AXES:
            if axis in judgment.not_applicable:
                continue
            score = getattr(judgment.scores, axis)
            if score is None:
                continue
            axis_values[axis].append(score)

        # 사용성 게이트: 이 카드는 안전을 재지 않으므로 늘 `safety_failed=False` 로 부른다.
        gate = derive_usability(judgment.scores, safety_failed=False)
        if gate.usable:
            usable += 1
        elif gate.reason == "safety":
            unusable_safety += 1
        elif gate.reason == "response_mode_fit":
            unusable_rmf += 1
        elif gate.reason == "repair_success":
            unusable_repair += 1

        if judgment.scores.response_mode_fit is not None:
            dead_end_n += 1
            if judgment.scores.response_mode_fit == 0:
                dead_end_count += 1

        if judgment.state_audit is not None:
            state_n += 1
            audit = judgment.state_audit
            state_avail += int(audit.relevant_state_available)
            state_used += int(audit.relevant_state_used)
            state_correct += int(audit.state_used_correctly)
            state_bad += int(audit.unsupported_or_superficial_personalization)

    axis_stats = {
        axis: AxisStat(
            n=len(values),
            mean=round(statistics.mean(values), 4) if values else None,
            distribution=dict(sorted(Counter(values).items())),
        )
        for axis, values in axis_values.items()
    }

    n_turns_total = len(lap_rows)
    excluded_before_judging_slots = int(judge_header.get("skipped", 0)) * len(AXES)
    numerator = excluded_before_judging_slots + fake_adapter_slots + not_applicable_slots
    denominator = n_turns_total * len(AXES)

    return Summary(
        lap=str(lap_meta.get("lap", "")),
        cases_sha256=str(lap_meta.get("cases_sha256", "")),
        judge_model=str(judge_header.get("judge_model", "")),
        prompt_version=int(judge_header.get("prompt_version", 0)),
        anchor_set=str(judge_header.get("anchor_set", lap_meta.get("anchor_set", ""))),
        adapter_mode=str(lap_meta.get("adapter_mode", "")),
        n_turns_total=n_turns_total,
        n_turns_judged=len(judgments),
        axis_stats=axis_stats,
        usability=UsabilityTally(
            usable=usable,
            unusable_safety=unusable_safety,
            unusable_response_mode_fit=unusable_rmf,
            unusable_repair_success=unusable_repair,
        ),
        state_audit=StateAuditTally(
            n_audited=state_n,
            relevant_state_available=state_avail,
            relevant_state_used=state_used,
            state_used_correctly=state_correct,
            unsupported_or_superficial_personalization=state_bad,
        ),
        unmeasured=UnmeasuredTally(
            numerator=numerator,
            denominator=denominator,
            excluded_before_judging_slots=excluded_before_judging_slots,
            fake_adapter_slots=fake_adapter_slots,
            not_applicable_slots=not_applicable_slots,
        ),
        dead_end_count=dead_end_count,
        dead_end_n=dead_end_n,
    )


# ---------------------------------------------------------------------------
# 렌더 — 결정론. 여기서도 아무것도 다시 안 잰다
# ---------------------------------------------------------------------------

_AXIS_LABEL = {
    "response_mode_fit": "response_mode_fit (모드 적합성)",
    "context_continuity": "context_continuity (이어짐)",
    "repair_success": "repair_success (복구)",
}


def _fmt_mean(mean: float | None) -> str:
    return "N/A" if mean is None else f"{mean:.2f}"


def _fmt_distribution(dist: Mapping[int, int]) -> str:
    if not dist:
        return "(없음)"
    return ", ".join(f"{score}점 {count}건" for score, count in sorted(dist.items()))


def render(summary: Summary) -> str:
    """`Summary` → 사람이 읽는 표. **총점 · 종합 점수를 만들지 않는다.**"""
    lines: list[str] = []
    lines.append(f"# 대화 품질 리포트 ({CARD}) — lap `{summary.lap}`")
    lines.append("")
    lines.append(
        f"고정: cases_sha256={summary.cases_sha256[:12]}… · judge_model={summary.judge_model} · "
        f"prompt_version={summary.prompt_version} · anchor_set={summary.anchor_set} · "
        f"adapter_mode={summary.adapter_mode}"
    )
    lines.append(f"턴 {summary.n_turns_total}개 중 판정 {summary.n_turns_judged}건")
    lines.append("")

    lines.append(
        "⚠ 세 축 모두 **not_calibrated** 입니다 (D-060 ⑦) — 사람 라벨로 맞춰 본 적이 없어서"
        " 이 표의 어떤 숫자도 지표가 아닙니다. 이 표가 하는 일은 채점이 아니라 사람이 볼 자리를"
        " 고르는 것입니다."
    )
    lines.append("")

    lines.append("## 축별 표 (평균 · 분포 · n) — 합계 없음")
    lines.append("")
    lines.append("| 축 | n | 평균 | 분포 |")
    lines.append("| --- | --- | --- | --- |")
    for axis in AXES:
        stat = summary.axis_stats[axis]
        lines.append(
            f"| {_AXIS_LABEL[axis]} | {stat.n} | {_fmt_mean(stat.mean)} | "
            f"{_fmt_distribution(stat.distribution)} |"
        )
    lines.append("")

    lines.append("## 사용성 게이트 집계")
    lines.append("")
    u = summary.usability
    lines.append(f"- usable: {u.usable}")
    lines.append(
        f"- unusable(safety): {u.unusable_safety} — ⚠ 이 카드는 안전 축이 없습니다. 0 은"
        " «안전 통과»가 아니라 **이 카드가 안전을 재지 않는다**는 뜻입니다."
    )
    lines.append(f"- unusable(response_mode_fit): {u.unusable_response_mode_fit}")
    lines.append(f"- unusable(repair_success): {u.unusable_repair_success}")
    lines.append("")

    lines.append("## 상태 감사 네 칸 — 사실 집계, 점수 아님")
    lines.append("")
    sa = summary.state_audit
    lines.append(f"- 감사한 턴 수: {sa.n_audited}")
    lines.append(f"- relevant_state_available: {sa.relevant_state_available}")
    lines.append(f"- relevant_state_used: {sa.relevant_state_used}")
    lines.append(f"- state_used_correctly: {sa.state_used_correctly}")
    lines.append(
        f"- unsupported_or_superficial_personalization: "
        f"{sa.unsupported_or_superficial_personalization}"
    )
    lines.append("")

    lines.append("## 미측정 비율")
    lines.append("")
    ratio = summary.unmeasured.ratio
    ratio_text = "N/A" if ratio is None else f"{ratio:.2%}"
    lines.append(
        f"- 미측정 {summary.unmeasured.numerator} / {summary.unmeasured.denominator} "
        f"({ratio_text}) — 분모는 턴 {summary.n_turns_total}개 × 축 3"
    )
    lines.append(f"  - 판정 전 제외: {summary.unmeasured.excluded_before_judging_slots}")
    lines.append(f"  - 가짜 어댑터 셀: {summary.unmeasured.fake_adapter_slots}")
    lines.append(f"  - 해당 없는 축: {summary.unmeasured.not_applicable_slots}")
    lines.append("")

    lines.append("## dead_end — 파생 진단 (축 아님)")
    lines.append("")
    lines.append(
        f"- {summary.dead_end_count} / {summary.dead_end_n} 턴이 `response_mode_fit == 0` 입니다."
    )
    lines.append(
        "  ⚠ 이것은 **진단**이지 축이 아닙니다 — 병원 안내처럼 형식상 다음 행동이 있는 막다른"
        " 길(`response_mode_fit` 이 2 여도 실제로는 대화가 끝나는 경우)은 이 수로 못 잡습니다."
    )
    lines.append("")

    lines.append(f"## 캘리브레이션 상태: **{summary.calibration}**")
    lines.append("")
    lines.append(
        "세 축(response_mode_fit · context_continuity · repair_success) 전부"
        f" `{summary.calibration}` 입니다 — 사람 라벨 캘리브레이션 전까지는 지표가 아닙니다"
        " (D-060 ⑦)."
    )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 전후 비교 — 여섯 고정 항목이 같아야 한다
# ---------------------------------------------------------------------------


def _pinned_value(summary: Summary, field: str) -> Any:
    return getattr(summary, field)


def _check_pins(before: Summary, after: Summary) -> None:
    for field in PINNED_FIELDS:
        b, a = _pinned_value(before, field), _pinned_value(after, field)
        if b != a:
            raise ValueError(
                f"비교를 거부합니다 — 고정 항목 `{field}` 이 움직였습니다: "
                f"before={b!r} after={a!r}. 움직인 핀 위의 비교는 결과처럼 보이는 잡음입니다."
            )


def render_compare(*, before: Summary, after: Summary) -> str:
    """두 랩을 견준다. **다섯 고정 항목 중 하나라도 다르면 거부한다.**"""
    _check_pins(before, after)

    lines: list[str] = []
    lines.append(f"# 대화 품질 전후 비교 ({CARD}) — `{before.lap}` → `{after.lap}`")
    lines.append("")
    lines.append(
        "고정 항목 확인: " + " · ".join(f"{f}={_pinned_value(before, f)}" for f in PINNED_FIELDS)
    )
    lines.append(
        "미측정 비율은 두 랩에서 같은 정의(판정 전 제외 + 가짜 어댑터 셀 + 해당 없는 축, "
        "분모=턴×3)로 계산했습니다."
    )
    lines.append("")

    lines.append("## 축별 평균 — before / after")
    lines.append("")
    lines.append("| 축 | before | after | 비고 |")
    lines.append("| --- | --- | --- | --- |")
    for axis in AXES:
        b_stat, a_stat = before.axis_stats[axis], after.axis_stats[axis]
        if axis in FLOORED_AXES:
            before_cell = f"{_FEATURE_ABSENT} ({_fmt_mean(b_stat.mean)})"
            note = (
                f"{_FEATURE_ABSENT} — 오늘 런타임은 무상태라 이 축의 정답이 0 으로 고정돼"
                " 있습니다 (`transcript.PRIOR_TURNS_REACH_INFERENCE is False`). after 의"
                " 숫자는 «모델이 좋아졌다»가 아니라 «기능이 새로 생겼다»는 뜻입니다."
            )
        else:
            before_cell = _fmt_mean(b_stat.mean)
            note = "실제 전후 비교 — before 에도 분산이 있습니다."
        lines.append(f"| {_AXIS_LABEL[axis]} | {before_cell} | {_fmt_mean(a_stat.mean)} | {note} |")
    lines.append("")

    lines.append("## 사용성 게이트 — before / after")
    lines.append("")
    lines.append("| | before | after |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| usable | {before.usability.usable} | {after.usability.usable} |")
    lines.append(
        f"| unusable(safety, 이 카드는 안 잼) | {before.usability.unusable_safety} | "
        f"{after.usability.unusable_safety} |"
    )
    lines.append(
        f"| unusable(response_mode_fit) | {before.usability.unusable_response_mode_fit} | "
        f"{after.usability.unusable_response_mode_fit} |"
    )
    lines.append(
        f"| unusable(repair_success) | {before.usability.unusable_repair_success} | "
        f"{after.usability.unusable_repair_success} |"
    )
    lines.append("")

    lines.append("## 상태 감사 — 사실 집계, before / after")
    lines.append("")
    lines.append("| | before | after |")
    lines.append("| --- | --- | --- |")
    for field in (
        "relevant_state_available",
        "relevant_state_used",
        "state_used_correctly",
        "unsupported_or_superficial_personalization",
    ):
        lines.append(
            f"| {field} | {getattr(before.state_audit, field)} | "
            f"{getattr(after.state_audit, field)} |"
        )
    lines.append("")

    lines.append("## 미측정 비율 — before / after")
    lines.append("")
    b_ratio, a_ratio = before.unmeasured.ratio, after.unmeasured.ratio
    b_txt = "N/A" if b_ratio is None else f"{b_ratio:.2%}"
    a_txt = "N/A" if a_ratio is None else f"{a_ratio:.2%}"
    lines.append(
        f"- before: {before.unmeasured.numerator}/{before.unmeasured.denominator} ({b_txt})"
    )
    lines.append(f"- after: {after.unmeasured.numerator}/{after.unmeasured.denominator} ({a_txt})")
    lines.append("")

    lines.append("## dead_end — 파생 진단, before / after")
    lines.append("")
    lines.append(
        f"- before: {before.dead_end_count}/{before.dead_end_n} · "
        f"after: {after.dead_end_count}/{after.dead_end_n}"
        " — 축이 아니라 진단입니다."
    )
    lines.append("")

    lines.append(f"## 캘리브레이션 상태: 둘 다 **{before.calibration}** / **{after.calibration}**")
    lines.append("")
    lines.append(
        "이 비교의 어떤 숫자도 지표가 아닙니다 — 사람이 볼 자리를 고르는 재료입니다 (D-060 ⑦)."
    )

    return "\n".join(lines) + "\n"
