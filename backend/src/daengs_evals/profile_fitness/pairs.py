"""셀 → 쌍. 조건 N/P/S 를 붙이고, 판정할 수 없는 쌍에 이유를 단다. **순수 함수**다.

    contrast   (q, arms[0], run0) × (q, arms[1], run0)   본 비교
    ablation   (q, "none",  run0) × (q, arms[0], run0)   프로필 없음 vs 있음 — 조건 P
    noise      (q, arms[0], run0) × (q, arms[0], run1)   같은 프로필 두 번 — 조건 N

조건 S(특이도)는 따로 수집하지 않는다 — invariant 질문의 contrast 쌍이 곧 S 다.

쌍마다 `skip_reason` 이 붙을 수 있다. **판정하지 않는 쌍과 판정은 하되 점수를 안 매기는 쌍은
다르다**: `noise` 쌍은 판정한다(그래야 `changed` 비율이 나온다) 하지만 `rubric.score_pair` 가
점수를 안 낸다. 반면 한쪽 셀이 없거나 답이 아니거나 프로필이 닿지 않는 능력이 답한 쌍은 판정
자체를 안 한다 — 판정기에게 FAILED 문구 두 개를 비교시키는 건 토큰 낭비이고, 그 결과를 세면
지표가 자기를 희석한다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from daengs_evals.profile_fitness.profiles import Profile, profiles_by_id
from daengs_evals.profile_fitness.questions import ABSENT_ARM, BASELINE, Question
from daengs_evals.profile_fitness.rubric import Kind

Condition = Literal["contrast", "ablation", "noise"]
SkipReason = Literal["missing_cell", "not_answered", "out_of_scope", "runner_error"]

#: 판정할 만한 최종 상태. PARTIAL 은 능력 하나가 답한 것이라 message 가 있다.
ANSWERED_STATUSES: frozenset[str] = frozenset({"ANSWERED", "PARTIAL"})


@dataclass(frozen=True)
class Pair:
    question_id: str
    query: str
    kind: Kind
    tier: str
    condition: Condition
    arm_a: str
    arm_b: str
    run_a: int
    run_b: int
    cell_a: Mapping[str, Any] | None
    cell_b: Mapping[str, Any] | None
    #: 두 셀에 답한 능력. 같아야 비교가 성립한다.
    capability: str | None
    skip_reason: SkipReason | None
    paraphrase_of: str | None = None

    @property
    def is_control(self) -> bool:
        return self.condition == "noise"

    @property
    def judgeable(self) -> bool:
        return self.skip_reason is None

    @property
    def pair_id(self) -> str:
        return f"{self.question_id}|{self.condition}|{self.arm_a}#{self.run_a}|{self.arm_b}#{self.run_b}"


def index_cells(
    cells: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str, int], Mapping[str, Any]]:
    index: dict[tuple[str, str, int], Mapping[str, Any]] = {}
    for cell in cells:
        key = (str(cell["question_id"]), str(cell["arm"]), int(cell["run"]))
        if key in index:
            raise ValueError(f"셀 중복: {key}")
        index[key] = cell
    return index


def answering_capability(cell: Mapping[str, Any]) -> str | None:
    """이 셀에 실제로 답한 능력 하나. 여럿이면 첫 번째 — v1 은 general 하나뿐이다."""
    for result in cell.get("results") or []:
        if result.get("status") == "OK":
            return str(result.get("capability"))
    caps = [c for c in cell.get("capabilities") or [] if not str(c).startswith("handoff:")]
    return caps[0] if len(caps) == 1 else None


#: build_pairs 가 _make_pair 에 넘기는 "진짜 어댑터" 집합 (모듈 내부 전달용)
_REAL: list[frozenset[str] | None] = [None]


def _reach(profile: Profile) -> set[str]:
    return set(profile.visible_to)


def _make_pair(
    q: Question,
    condition: Condition,
    a: tuple[str, int],
    b: tuple[str, int],
    index: Mapping[tuple[str, str, int], Mapping[str, Any]],
    by_id: Mapping[str, Profile],
) -> Pair:
    cell_a = index.get((q.question_id, a[0], a[1]))
    cell_b = index.get((q.question_id, b[0], b[1]))
    capability: str | None = None
    skip: SkipReason | None = None

    if cell_a is None or cell_b is None:
        skip = "missing_cell"
    elif cell_a.get("error") or cell_b.get("error"):
        skip = "runner_error"
    elif (
        cell_a.get("status") not in ANSWERED_STATUSES
        or cell_b.get("status") not in ANSWERED_STATUSES
    ):
        skip = "not_answered"
    else:
        cap_a, cap_b = answering_capability(cell_a), answering_capability(cell_b)
        if cap_a is None or cap_a != cap_b:
            # 두 답이 다른 능력에서 왔으면 차이의 원인이 프로필이 아니다
            skip = "out_of_scope"
        elif _REAL[0] is not None and cap_a not in _REAL[0]:
            skip = "out_of_scope"  # 가짜 어댑터의 자리표시 답 — 판정할 것이 없다
        else:
            capability = cap_a
            reach = _reach(by_id[a[0]]) & _reach(by_id[b[0]])
            if capability not in reach:
                skip = "out_of_scope"

    return Pair(
        question_id=q.question_id,
        query=q.query,
        kind=q.kind,
        tier=q.tier,
        condition=condition,
        arm_a=a[0],
        arm_b=b[0],
        run_a=a[1],
        run_b=b[1],
        cell_a=cell_a,
        cell_b=cell_b,
        capability=capability,
        skip_reason=skip,
        paraphrase_of=q.paraphrase_of,
    )


def build_pairs(
    questions: Iterable[Question],
    profiles: Iterable[Profile],
    cells: Iterable[Mapping[str, Any]],
    *,
    conditions: Iterable[Condition] = ("contrast", "ablation", "noise"),
    real_capabilities: frozenset[str] | None = None,
) -> list[Pair]:
    """조건별 쌍. 셀이 없는 쌍도 만든다(`skip_reason="missing_cell"`) — 리포트가 "몇 개를
    못 쟀는가" 를 셀 수 있어야 하고, 없는 것을 조용히 빼면 그 수가 사라진다.

    `real_capabilities` 는 이 수집에서 **진짜 어댑터**였던 능력. `fallback-only` 면 {"general"} 뿐이고,
    training · life 는 "(가짜 training 어댑터)" 같은 자리표시를 ANSWERED 로 돌려준다. 그 셀을 판정하면
    자리표시 두 개를 비교하게 된다 — 2026-09-09 사람 라벨에서 드러났다("가짜 life 어댑터가 뭔지
    모르겠다"). 그런 쌍은 out_of_scope 다.
    """
    index = index_cells(cells)
    by_id = profiles_by_id(profiles)
    wanted = set(conditions)
    _REAL[0] = real_capabilities
    pairs: list[Pair] = []
    for q in questions:
        base = q.arms[BASELINE]
        other = q.arms[1 - BASELINE]
        if "contrast" in wanted:
            pairs.append(_make_pair(q, "contrast", (base, 0), (other, 0), index, by_id))
        if "ablation" in wanted and q.kind == "reactive":
            pairs.append(_make_pair(q, "ablation", (ABSENT_ARM, 0), (base, 0), index, by_id))
        if "noise" in wanted:
            pairs.append(_make_pair(q, "noise", (base, 0), (base, 1), index, by_id))
    return pairs


def summarize_pairs(pairs: Iterable[Pair]) -> dict[str, Any]:
    from collections import Counter

    ps = list(pairs)
    return {
        "total": len(ps),
        "judgeable": sum(1 for p in ps if p.judgeable),
        "by_condition": dict(Counter(p.condition for p in ps)),
        "skipped": dict(Counter(p.skip_reason for p in ps if p.skip_reason)),
        "by_capability": dict(Counter(p.capability for p in ps if p.capability)),
    }


__all__ = [
    "ANSWERED_STATUSES",
    "Pair",
    "answering_capability",
    "build_pairs",
    "index_cells",
    "summarize_pairs",
]
