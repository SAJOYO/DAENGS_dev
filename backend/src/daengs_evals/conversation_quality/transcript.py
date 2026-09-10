"""판정기를 부르기 전에 코드가 정하는 것.

**코드 검사는 fan-out 이 아니라 필터다.** 통과한 행만 판정기로 간다 — 빈 답이나
게이트가 닫아 버린 턴을 판정시키면 판정자가 우리 상수를 채점한다 (D-060 ⑤).

`PRIOR_TURNS_REACH_INFERENCE` 는 이 패키지에서 가장 중요한 한 줄이다. 오늘 False 이고,
그래서 `context_continuity` · `repair_success` 의 정답이 0 으로 확정돼 있다.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, ConfigDict

from daengs_backend.orchestration.redirects import (
    NO_CAPABILITY_MESSAGE,
    SCOPED_REDIRECT_MESSAGES,
)

#: `routers/assistant.py` 가 `service.run(query=body.query, ...)` 로 현재 질의만 넘기고
#: `services/chat.py:run_persisted_turn` 은 저장만 한다. 런타임이 바뀌면 이 상수를 고치고
#: 두 축의 기대 정답을 다시 정한다. (`tests` 가 이 상수와 리포트 문구를 함께 잡는다.)
PRIOR_TURNS_REACH_INFERENCE = False

_FIXED_REFUSALS = frozenset(SCOPED_REDIRECT_MESSAGES.values())


class CodeChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_repeat_count: int
    refusal_source_ambiguous: bool
    fixed_refusal_turns: int
    excluded_before_judging: dict[str, int]


def check_transcript(*, assistant_texts: list[str]) -> CodeChecks:
    counts = Counter(t.strip() for t in assistant_texts if t.strip())
    excluded: dict[str, int] = {}
    empty = sum(1 for t in assistant_texts if not t.strip())
    if empty:
        excluded["not_answered"] = empty
    return CodeChecks(
        max_repeat_count=max(counts.values(), default=0),
        # 같은 문장이 General 거절과 빈 계획 FAILED 두 곳에서 나온다 — 문장만으로는 못 가린다
        refusal_source_ambiguous=any(t.strip() == NO_CAPABILITY_MESSAGE for t in assistant_texts),
        fixed_refusal_turns=sum(1 for t in assistant_texts if t.strip() in _FIXED_REFUSALS),
        excluded_before_judging=excluded,
    )
