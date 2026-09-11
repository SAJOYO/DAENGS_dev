"""판정기 없이 코드만으로 답할 수 있는 사실 몇 가지.

**이 모듈은 판정 전 필터가 아니다.** 빈 답변·`NOT_REACHED` 행을 판정기 앞에서 거르는 것은
`judge.run_score` 가 직접 한다(그 행을 판정기에 먹이면 판정자가 우리 상수를 채점하기
때문이다 — D-060 ⑤). 여기 `check_transcript` 가 하는 일은 그것과 다르다: **이미 있는 답변
텍스트에서 코드로 뽑을 수 있는 사실**(같은 문장이 몇 번 반복됐는지, 거절 문장의 출처가
문장만으로 가려지는지)을 계산해서 `report.summarize` 가 케이스별로 보여줄 수 있게 하는
자리다. 채점하지 않는다 — 판정 축도 아니다.

`PRIOR_TURNS_REACH_INFERENCE` 는 이 패키지에서 가장 중요한 한 줄이다. `#416` 이 Turn
Resolver 를 놓고 `SessionDriver`(`drivers.py`)가 `prior_turns`·`pending_clarification`
을 실제로 실어 보내면서 True 로 뒤집혔다 — `StatelessDriver` 로 모은 이전 랩(`before`)
에서는 여전히 False 인 세계를 잰 것이라 그쪽 두 축 정답은 그대로 0 이다(`report.py` 의
`FLOORED_AXES` 가 그 랩을 그렇게 고정해 둔다). `SessionDriver` 로 모으는 랩부터는 이
가정이 사라지므로, `context_continuity` · `repair_success` 를 더는 코드로 0 이라
단정하지 않고 판정기가 실제로 재게 한다.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, ConfigDict

#: `#416` 으로 `AssistantOrchestrationService.run` 이 `prior_turns`·`pending_clarification`
#: 을 실제로 받아 `GeminiTurnResolver` 에 넘기게 됐고, `SessionDriver` 가 그 계약대로
#: 이전 턴을 실어 보낸다 — 그래서 이 런타임을 무는 랩에서는 이전 턴이 추론에 닿는다.
#: `StatelessDriver` 로 모은(=이전 턴을 안 싣는) 랩만 여전히 두 축의 정답이 0 이다.
#: (`tests` 가 이 상수와 리포트 문구를 함께 잡는다.)
PRIOR_TURNS_REACH_INFERENCE = True


def _fixed_refusal_texts() -> tuple[str, frozenset[str]]:
    """지연 읽기 — `judge.client` · `report._fixed_refusals` 와 같은 자리.

    `daengs_backend.orchestration.redirects` 자체는 문자열 상수만 든 순수 모듈이지만,
    최상단에서 import 하면 그 위 패키지 `__init__`(→ `graph` → `planner` → `semantic` →
    `daengs_backend.config`)이 통째로 딸려 와서 **이 모듈을 import 만 해도** DB 접속
    정보 · 암호화 키가 있어야 뜬다. `import daengs_evals.conversation_quality.transcript`
    는 판정 전 필터 하나를 쓰려는 것이지 오케스트레이터를 조립하려는 것이 아니므로,
    `check_transcript` 가 실제로 이 값을 쓸 때만 늦게 물어서 import 자체는 설정 없이도
    된다.
    """
    from daengs_backend.orchestration.redirects import (
        NO_CAPABILITY_MESSAGE,
        SCOPED_REDIRECT_MESSAGES,
    )

    return NO_CAPABILITY_MESSAGE, frozenset(SCOPED_REDIRECT_MESSAGES.values())


class CodeChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_repeat_count: int
    refusal_source_ambiguous: bool
    fixed_refusal_turns: int
    excluded_before_judging: dict[str, int]


def check_transcript(*, assistant_texts: list[str]) -> CodeChecks:
    no_capability_message, fixed_refusals = _fixed_refusal_texts()
    counts = Counter(t.strip() for t in assistant_texts if t.strip())
    excluded: dict[str, int] = {}
    empty = sum(1 for t in assistant_texts if not t.strip())
    if empty:
        excluded["not_answered"] = empty
    return CodeChecks(
        max_repeat_count=max(counts.values(), default=0),
        # 같은 문장이 General 거절과 빈 계획 FAILED 두 곳에서 나온다 — 문장만으로는 못 가린다
        refusal_source_ambiguous=any(t.strip() == no_capability_message for t in assistant_texts),
        fixed_refusal_turns=sum(1 for t in assistant_texts if t.strip() in fixed_refusals),
        excluded_before_judging=excluded,
    )
