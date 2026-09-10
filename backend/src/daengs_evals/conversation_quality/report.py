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

────────────────────────────────────────────────────────────────────────────
`dead_end` — 부분 신호다, 전체가 아니다
────────────────────────────────────────────────────────────────────────────
처음 버전은 `response_mode_fit == 0` 을 셌다. 그런데 `applicability` 가 이 축을 늘
True 로 두므로 그 수는 **축별 표의 0점 칸 · `usability.unusable_response_mode_fit`
과 같은 값을 이름만 셋으로 부르는 것**이었다 — `rubric.py` 가 진짜 걱정한 경우(형식상
다음 행동이 있어 `response_mode_fit` 이 2 여도 실제로는 막다른 병원 안내)는 애초에 그
정의로는 못 걸렀다.

지금 정의는 그 경우를 실제로 겨눈다: **답이 `daengs_backend.orchestration.redirects.
SCOPED_REDIRECT_MESSAGES` 의 고정 리다이렉트 문구와 같고, 동시에 `response_mode_fit`
이 0 이 아니다.** 모드는 괜찮다고 판정됐는데 사용자가 받은 것이 정형 문구 한 줄뿐인
자리 — 그것이 "형식상 다음 행동은 있는데 상호작용으로는 막다른 길"의 확인 가능한
부분집합이다.

**그래도 부분 신호다.** 고정 문구가 아닌 답으로 똑같이 막다른 자리(모델이 매번 다른
말로 같은 벽을 세우는 경우)는 이 신호로 못 잡는다 — 렌더 문구와 `Summary.dead_end_*`
docstring 이 그것을 명시한다. 못 잡는 부분은 여전히 사람이 대화를 읽어야 한다.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from daengs_evals.conversation_quality.judge import AXES, TurnJudgment
from daengs_evals.conversation_quality.rubric import derive_usability
from daengs_evals.conversation_quality.transcript import check_transcript


def _fixed_refusals() -> frozenset[str]:
    """지연 읽기 — `judge.client` · `judge.judge_model` 과 같은 자리.

    `daengs_backend.orchestration.redirects` 를 최상단에서 import 하면 그 모듈이 물고 있는
    `daengs_backend.orchestration` 패키지 전체(→ `planner` → `semantic` → `config.settings`)가
    딸려 와서, **`report` 모듈을 import 만 해도** DB 접속 정보 · 암호화 키가 있어야 뜬다 —
    `report`·`compare` 는 "이미 있는 파일만 읽는다"는 이 모듈의 약속과 어긋난다. `summarize`
    가 실제로 이 값을 쓸 때만 늦게 물어서, `render`·`render_compare` 처럼 이미 만든
    `Summary` 만 다루는 자리는 그 설정 없이도 계속 동작한다.
    """
    from daengs_backend.orchestration.redirects import SCOPED_REDIRECT_MESSAGES

    return frozenset(SCOPED_REDIRECT_MESSAGES.values())


def _try_fixed_refusals() -> tuple[frozenset[str], bool]:
    """`_fixed_refusals()` 를 죽지 않게 부른다.

    `report`·`compare` 는 "이미 있는 파일만 읽는다"는 약속인데, `_fixed_refusals` 가
    실제로는 `daengs_backend.config.settings` 를 요구한다(DB 접속 정보 · 암호화 키) —
    체크아웃에 `backend/.env` 가 없으면 이 두 명령이 파일만 읽다가 죽는다. 그 실패를 여기서
    삼키고 `dead_end` 를 "0 건" 이 아니라 **미측정**으로 보고한다 — 0 은 "쟀는데 없었다"로
    읽히는데 실제로는 잰 적이 없다.
    """
    try:
        return _fixed_refusals(), True
    except RuntimeError:
        return frozenset(), False


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

#: `LapHeader` 와 `JudgeHeader` 가 **이름이 같은 값**을 각자 따로 적는 세 자리. `collect.py`
#: 는 "이 조건으로 판정할 생각이다"라는 계획을 적고, `judge.header()` 는 실제로 무엇으로
#: 판정했는지를 적는다 — 둘은 서로 다른 시점에 쓰이므로 저절로 맞는다는 보장이 없다.
#: `score --judge-model X` 를 `judge_model=Y` 라고 적힌 랩에 대고 돌리면 판정 파일은 X 를
#: 정직하게 적지만, 아무도 그것이 랩의 계획과 어긋났다고 말해 주지 않는다 — 그 조용한
#: 어긋남을 여기서 잡는다. 랩이 이 값을 아예 안 적은 자리(옛 랩 · 손으로 만든 테스트 fixture)
#: 는 검사하지 않는다 — 없음과 다름은 다르다.
_SHARED_HEADER_PINS: tuple[str, ...] = ("judge_model", "prompt_version", "anchor_set")


def _check_shared_header_pins(lap_meta: Mapping[str, Any], judge_header: Mapping[str, Any]) -> None:
    for field in _SHARED_HEADER_PINS:
        if field not in lap_meta:
            continue
        lap_val, judge_val = lap_meta[field], judge_header.get(field)
        if lap_val != judge_val:
            raise ValueError(
                f"랩과 판정 파일이 `{field}` 에서 어긋났습니다: "
                f"랩={lap_val!r} 판정={judge_val!r}. 이 판정 파일은 이 랩을 판정한 것이"
                " 아니거나, 랩이 선언한 조건과 다른 모델·설정으로 판정됐습니다."
            )


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


class CaseCodeCheck(BaseModel):
    """`transcript.check_transcript` 가 케이스 하나에 대해 낸 것 중 리포트가 보이는 두 칸.

    **판정이 아니라 사실이다.** `max_repeat_count` 는 같은 답이 몇 번 되풀이됐는지, 판정기가
    아니라 코드가 문자열을 세어서 낸 값이고, `refusal_source_ambiguous` 는 그 케이스에
    `daengs_backend.orchestration.redirects.NO_CAPABILITY_MESSAGE` 와 글자 그대로 같은
    답이 있었는지다(General 의 off-topic 거절과 빈 계획 FAILED 가 같은 문장을 내서 문장만
    으로는 어느 쪽인지 못 가린다)."""

    model_config = ConfigDict(extra="forbid")

    max_repeat_count: int
    refusal_source_ambiguous: bool


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
    #: 판정 축이 아니라 **부분** 파생 진단이다 — 답이 고정 리다이렉트 문구
    #: (`daengs_backend.orchestration.redirects.SCOPED_REDIRECT_MESSAGES`)와 같은데
    #: `response_mode_fit` 은 0 이 아닌 턴만 센다("모드는 괜찮다고 판정됐는데 사용자가
    #: 받은 것은 정형 문구 한 줄"). **모델이 매번 다른 말로 세우는 막다른 길은 이 수로
    #: 못 잡는다** — 위 모듈 docstring "`dead_end` — 부분 신호다" 참고.
    dead_end_count: int
    dead_end_n: int
    #: `False` 면 위 둘은 "0 건" 이 아니라 **잰 적이 없다** — `backend/.env` 가 없어
    #: `_fixed_refusals` 가 못 뜬 환경에서 리포트가 죽는 대신 이 값을 내린다.
    dead_end_measured: bool = True
    #: 케이스 아이디 → 코드 기반 사실 두 칸(`transcript.check_transcript`). 판정 축이
    #: 아니다 — 판정기를 부르지 않고 랩 행의 답 텍스트만 센다.
    code_checks: dict[str, CaseCodeCheck] = Field(default_factory=dict)
    #: `dead_end_measured` 와 같은 이유로 `code_checks` 가 비어 있을 수 있다.
    code_checks_measured: bool = True
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
    _check_shared_header_pins(lap_meta, judge_header)
    fake_keys = {
        (str(row.get("case_id")), int(row.get("turn_index", -1)))
        for row in lap_rows
        if row.get("answered_by_fake_adapter") is True
    }
    #: `dead_end` 가 "이 답이 고정 리다이렉트 문구였나"를 묻으려면 판정이 아니라 랩 행의
    #: 실제 답 텍스트가 있어야 한다 — `TurnJudgment` 는 답 텍스트를 안 들고 있다.
    messages_by_key = {
        (str(row.get("case_id")), int(row.get("turn_index", -1))): str(row.get("message", ""))
        for row in lap_rows
    }
    fixed_refusals, settings_available = _try_fixed_refusals()

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

        # `response_mode_fit` 은 `applicability` 가 늘 True 로 두는 축이라(`AxisScores` 에서도
        # 기본값 없는 필수 `int`) 여기서 세는 분모는 사실상 "판정된(가짜 아닌) 턴 수" 다 —
        # `axis_stats['response_mode_fit'].n` 과 값이 같아 보여도 우연이 아니라 정의가 같기
        # 때문이고, 이 진단이 새로 재는 것은 분자(정형 문구 + 모드 통과) 쪽이다.
        rmf = judgment.scores.response_mode_fit
        if settings_available:
            dead_end_n += 1
            message = messages_by_key.get(key, "").strip()
            if message in fixed_refusals and rmf != 0:
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

    # 케이스별 코드 기반 검사 — `check_transcript` 도 같은 lazy import 를 겪으므로
    # `_fixed_refusals` 가 못 뜬 환경에서는 여기도 같이 미측정으로 내린다.
    code_checks: dict[str, CaseCodeCheck] = {}
    if settings_available:
        assistant_texts_by_case: dict[str, list[str]] = {}
        for row in lap_rows:
            case_id = str(row.get("case_id"))
            assistant_texts_by_case.setdefault(case_id, []).append(str(row.get("message", "")))
        for case_id, assistant_texts in assistant_texts_by_case.items():
            checks = check_transcript(assistant_texts=assistant_texts)
            code_checks[case_id] = CaseCodeCheck(
                max_repeat_count=checks.max_repeat_count,
                refusal_source_ambiguous=checks.refusal_source_ambiguous,
            )

    return Summary(
        lap=str(lap_meta.get("lap", "")),
        cases_sha256=str(lap_meta.get("cases_sha256", "")),
        judge_model=str(judge_header.get("judge_model", "")),
        prompt_version=int(judge_header.get("prompt_version", 0)),
        # `judge.header()` 가 `anchor_set` 을 늘 채워 쓴다 — `lap_meta` 로의 대체 경로는
        # 안 둔다. 대체 경로를 두면 판정 헤더가 비어 있어도 조용히 랩 헤더 값으로 넘어가고,
        # 그러면 "어느 앵커로 통과했는지" 를 판정 파일이 스스로 말 못 하는 자리가 하나 생긴다.
        anchor_set=str(judge_header.get("anchor_set", "")),
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
        dead_end_measured=settings_available,
        code_checks=code_checks,
        code_checks_measured=settings_available,
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
    lines.append(
        f"  - 판정 전 제외: {summary.unmeasured.excluded_before_judging_slots}"
        " (빈 답변 · NOT_REACHED — judge 를 아예 안 불렀다)"
    )
    lines.append(
        f"  - 가짜 어댑터 셀: {summary.unmeasured.fake_adapter_slots}"
        " (판정은 됐지만 자리표시자를 채점한 것)"
    )
    lines.append(
        f"  - 해당 없는 축: {summary.unmeasured.not_applicable_slots}"
        " (잴 수 없어서 애초에 안 불렀다 — 실패가 아니다)"
    )
    ratio = summary.unmeasured.ratio
    ratio_text = "N/A" if ratio is None else f"{ratio:.2%}"
    lines.append(
        f"- 미측정 {summary.unmeasured.numerator} / {summary.unmeasured.denominator} "
        f"({ratio_text}) — 분모는 턴 {summary.n_turns_total}개 × 축 3. ⚠ 이 하나의 비율은"
        " 위 세 가지 서로 다른 사정(judge 가 안 불렀다 · 자리표시자였다 · 애초에 해당 없다)을"
        " 섞은 값입니다 — 위 항목별 수를 먼저 보고, 이 비율만 따로 인용하지 마세요."
    )
    lines.append("")

    lines.append("## dead_end — 부분 파생 진단 (축 아님)")
    lines.append("")
    if not summary.dead_end_measured:
        lines.append(
            "  ⚠ 측정 불가 — backend 설정(`backend/.env`)이 없어 고정 리다이렉트 문구를"
            " 대조하지 못했습니다. 0 건이 아니라 **잰 적이 없다**는 뜻입니다."
        )
    else:
        lines.append(
            f"- {summary.dead_end_count} / {summary.dead_end_n} 턴이 고정 리다이렉트 문구"
            "(`daengs_backend.orchestration.redirects.SCOPED_REDIRECT_MESSAGES`)로 답했으면서"
            " `response_mode_fit` 은 0 이 아니었습니다 — 모드는 괜찮다고 판정됐는데 사용자가"
            " 받은 것은 정형 문구 한 줄뿐이었던 자리입니다."
        )
        lines.append(
            "  ⚠ 이것은 **진단**이지 축이 아니고, 그나마도 **부분 신호**입니다 — 병원 안내처럼"
            " 형식상 다음 행동이 있는 막다른 길 중 **고정 문구가 아닌 것**(모델이 매번 다른 말로"
            " 같은 벽을 세우는 경우)은 이 수로 못 잡습니다."
        )
    lines.append("")

    lines.append("## 코드 기반 검사 — 케이스별 사실 (판정 아님)")
    lines.append("")
    if not summary.code_checks_measured:
        lines.append("  ⚠ 측정 불가 — backend 설정(`backend/.env`)이 없어 계산하지 못했습니다.")
    elif not summary.code_checks:
        lines.append("  (케이스 없음)")
    else:
        lines.append("| 케이스 | 최다 반복 횟수 | 거절 출처 모호 |")
        lines.append("| --- | --- | --- |")
        for case_id in sorted(summary.code_checks):
            check = summary.code_checks[case_id]
            lines.append(
                f"| {case_id} | {check.max_repeat_count} | {check.refusal_source_ambiguous} |"
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


def _check_pins(before: Summary, after: Summary) -> None:
    for field in PINNED_FIELDS:
        b, a = getattr(before, field), getattr(after, field)
        if b != a:
            raise ValueError(
                f"비교를 거부합니다 — 고정 항목 `{field}` 이 움직였습니다: "
                f"before={b!r} after={a!r}. 움직인 핀 위의 비교는 결과처럼 보이는 잡음입니다."
            )


def render_compare(*, before: Summary, after: Summary) -> str:
    """두 랩을 견준다. **다섯 고정 항목 중 하나라도 다르면 거부한다.**"""
    _check_pins(before, after)

    def _fmt_pin(field: str) -> str:
        value = getattr(before, field)
        # `render()` 와 같은 길이로 줄인다 — 여기서만 64 자 전체를 보이면 같은 값이
        # 두 렌더에서 다른 모양으로 찍혀, 사람이 눈으로 대조할 때 헷갈린다.
        return f"{field}={value[:12]}…" if field == "cases_sha256" else f"{field}={value}"

    lines: list[str] = []
    lines.append(f"# 대화 품질 전후 비교 ({CARD}) — `{before.lap}` → `{after.lap}`")
    lines.append("")
    lines.append("고정 항목 확인: " + " · ".join(_fmt_pin(f) for f in PINNED_FIELDS))
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
    lines.append(
        f"  - 판정 전 제외: {before.unmeasured.excluded_before_judging_slots} → "
        f"{after.unmeasured.excluded_before_judging_slots}"
    )
    lines.append(
        f"  - 가짜 어댑터 셀: {before.unmeasured.fake_adapter_slots} → "
        f"{after.unmeasured.fake_adapter_slots}"
    )
    lines.append(
        f"  - 해당 없는 축: {before.unmeasured.not_applicable_slots} → "
        f"{after.unmeasured.not_applicable_slots}"
    )
    b_ratio, a_ratio = before.unmeasured.ratio, after.unmeasured.ratio
    b_txt = "N/A" if b_ratio is None else f"{b_ratio:.2%}"
    a_txt = "N/A" if a_ratio is None else f"{a_ratio:.2%}"
    lines.append(
        f"- 미측정 비율(세 사정을 섞은 값, 위 항목별 수를 먼저 보세요): "
        f"before {before.unmeasured.numerator}/{before.unmeasured.denominator} ({b_txt}) · "
        f"after {after.unmeasured.numerator}/{after.unmeasured.denominator} ({a_txt})"
    )
    lines.append("")

    lines.append("## dead_end — 부분 파생 진단, before / after")
    lines.append("")

    def _dead_end_cell(summary: Summary) -> str:
        if not summary.dead_end_measured:
            return "측정 불가(settings 없음)"
        return f"{summary.dead_end_count}/{summary.dead_end_n}"

    lines.append(
        f"- before: {_dead_end_cell(before)} · after: {_dead_end_cell(after)}"
        " — 축이 아니라 진단이고, 고정 리다이렉트 문구인 경우만 잡는 부분 신호입니다."
    )
    lines.append("")

    lines.append(f"## 캘리브레이션 상태: 둘 다 **{before.calibration}** / **{after.calibration}**")
    lines.append("")
    lines.append(
        "이 비교의 어떤 숫자도 지표가 아닙니다 — 사람이 볼 자리를 고르는 재료입니다 (D-060 ⑦)."
    )

    return "\n".join(lines) + "\n"
