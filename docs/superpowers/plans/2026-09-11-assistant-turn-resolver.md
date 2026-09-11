# Turn Resolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 라우팅 앞에 자리 하나를 두어 현재 발화를 `NEW`·`FOLLOW_UP`·`CORRECTION`·`REPEAT`·`META` 로 가르고, 앞 요청이나 대기 중인 되묻기에 이어, 제한된 구조화 컨텍스트를 라우터와 선택된 capability 에 넘긴다.

**Architecture:** `orchestration/resolver.py` 한 파일이 관계 판정을 소유한다. `emergency.py`·`semantic.py` 와 같은 층의 형제다. 순서는 **응급 → 결정론 → Resolver → 시맨틱 라우터**이고, 응급이 Resolver 보다 앞이라 현재 원문을 직접 검사한다. 맥락 의존 신호가 없으면 모델을 안 태우고 `NEW` 로 끝낸다(fast path). 이력 원문은 Resolver 에서 멈추고, 그 아래로는 `ConversationContext` 만 간다.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 async · Pydantic v2 (`ContractModel`) · Gemini (`gemini-3.1-flash-lite`) · pytest

**Spec:** `docs/superpowers/specs/2026-09-10-assistant-turn-context-design.md`

## Global Constraints

- **`uv` 를 거쳐 실행한다.** `cd backend && uv run pytest …`. 의존성은 `uv add`(이 계획은 새 의존성이 없다).
- **공유 열거형을 안 넓힌다.** `AssistantStatus` · `CapabilityStatus` · `CapabilityName` 은 그대로. `TurnRelation` 은 resolver 의 것이다.
- **`RoutePlan.clarify` 를 새로 채우지 않는다.** 배타성 검증기 둘(`contracts.RoutePlan.clarify_is_exclusive` · `graph._validate_route_plan`)이 그대로 서야 한다. 되묻기는 D-068 ①′ 대로 **집계**에 산다.
- **`LOGGER` 에 질의·이력 원문을 절대 안 남긴다** (D-037 · D-048). 남기는 것은 `request_id` · 상태 · 오류 코드뿐.
- **`relation=NEW` 면 프롬프트가 바이트 동일**해야 한다. 이것이 「무이력 경로가 안 깨졌다」의 코드 형태다.
- 한도 세 숫자는 같이 움직인다: user 2,000자(DB 제약) · assistant 400자 · 후보 블록 3,000자.
- **모델이 만든 `standalone_query` 를 반려견·사용자의 확정 사실로 저장하지 않는다.** 원문과 출처 turn id 를 항상 같이 들고 다닌다.
- **되묻기 turn 의 `results` 는 비어 있다** (D-068 진리표). 직전 턴에서 "어느 능력이 돌았나" 를
  `public_response["results"]` 로 알아내려 하면 **조용히 빈 손이 된다** — `clarify` 필드로 안다.
- **`clarify.missing` 은 General ask 에서 늘 `["observation"]`** 이라 되묻기의 부류를 못 가른다
  (`GENERAL_ASK_MISSING` 하드코딩). 부류를 가르는 것은 **`missing_axes` 의 비어 있음**뿐이고,
  비어 있으면 **축을 모르는 것**이다 — 코드가 축을 메우면 안 된다(#415 의 계약).
- 머지 전 `cd backend && uv run check` (3초) · 백엔드를 건드렸으므로 `uv run pytest` 까지.

## File Structure

| 파일 | 책임 |
| --- | --- |
| `backend/src/daengs_backend/orchestration/resolver.py` (신규) | `TurnRelation` · `PriorTurn` · `PendingClarification` · `ConversationContext` · `ResolvedTurn` · fast path 규칙 · 후보 블록 렌더 · 프롬프트 · 출력 검증 · `GeminiTurnResolver` |
| `orchestration/contracts.py` | `ConversationContext` 를 `GeneralPayload` 에 한 칸 추가. 그 외 무변경 |
| `orchestration/service.py` | `run(...)` 에 인자 둘, `_plan_and_execute` 에 Resolver 한 단계 |
| `orchestration/semantic.py` | `build_semantic_router_prompt(..., resolved=)` · `select(..., resolved=)` · 조건부 프롬프트 버전 |
| `orchestration/planner.py` | `assemble_route_plan(..., resolved=)` → `_payload_for` 가 `GeneralPayload.conversation` 을 채움 |
| `orchestration/adapters/general.py` | `CONVERSATION:` 블록(있을 때만) · 조건부 프롬프트 버전 |
| `repositories/chat.py` | `list_recent_completed_turns(session, session_id, *, limit)` |
| `services/chat.py` | `candidates_of` · `pending_clarification_of` · `run_persisted_turn` 이 둘을 읽어 `orchestrate` 로 넘김 |
| `routers/assistant.py` | `orchestrate` 콜백 시그니처 |
| `daengs_evals/conversation_quality/drivers.py` | `SessionDriver` |
| `daengs_evals/conversation_quality/collect.py` | `build_session_driver` |
| `daengs_evals/conversation_quality/transcript.py` | `PRIOR_TURNS_REACH_INFERENCE = True` |

### 스펙에서 고치는 것 하나 — 조건부 프롬프트 버전

스펙 §6 은 `semantic-router-ko-v10 → v11` 로 **무조건** 올린다고 적었다. 그러면 버전 문자열 하나가 두 가지 몸(블록 있음/없음)을 가리켜, 랩 헤더의 핀이 거짓말을 한다. `general.py` 가 네 조합에 네 상수를 둔 이유가 정확히 그것이다.

**그래서 조건부로 간다.** 기존 상수는 **그대로 두고**, 컨텍스트가 실릴 때만 접미사가 붙은 값을 쓴다:

- `semantic.PROMPT_VERSION` = `"semantic-router-ko-v10"` (무변경) / 실릴 때 `"semantic-router-ko-v10-resolved"`
- General 은 네 상수 그대로 / 실릴 때 각각 `f"{base}-conv"`

부수 효과로 라우터 벤치마크 기준선이 **문자 그대로** 안 움직인다.

---

### Task 1: Resolver 계약과 `NEW` fast path

**Files:**
- Create: `backend/src/daengs_backend/orchestration/resolver.py`
- Test: `backend/tests/test_orchestration_turn_resolver.py`

**Interfaces:**
- Consumes: `daengs_backend.orchestration.contracts.ContractModel`, `ObservationAxis`
- Produces: `TurnRelation`, `PriorTurn`, `PendingClarification`, `ConversationContext`, `ResolvedTurn`, `needs_resolution(...) -> bool`, `new_turn(query) -> ResolvedTurn`, 상수 `MAX_CANDIDATE_PAIRS=3` · `MAX_ASSISTANT_CHARS=400` · `MAX_CANDIDATE_BLOCK_CHARS=3000` · `RESOLUTION_CONFIDENCE_FLOOR=0.6`

- [ ] **Step 1: 실패하는 테스트를 쓴다 — 수용 케이스 1·7 (fast path)**

```python
# backend/tests/test_orchestration_turn_resolver.py
import uuid

import pytest

from daengs_backend.orchestration.contracts import ObservationAxis
from daengs_backend.orchestration.resolver import (
    PendingClarification,
    PriorTurn,
    ResolvedTurn,
    TurnRelation,
    needs_resolution,
    new_turn,
)


def _turn(user: str, assistant: str) -> PriorTurn:
    return PriorTurn(turn_id=uuid.uuid4(), user=user, assistant=assistant)


def test_no_candidates_needs_no_resolution() -> None:
    """수용 케이스 1 — 이력 없는 독립 질문은 모델을 안 태운다."""
    assert needs_resolution(query="강아지 사료 추천해줘", candidates=(), pending=None) is False


def test_unrelated_old_history_needs_no_resolution() -> None:
    """수용 케이스 7 — 오래된 무관한 이력은 현재 라우팅을 안 건드린다."""
    candidates = [_turn("산책 코스 추천해줘", "근처 공원을 추천합니다.")]
    assert (
        needs_resolution(query="심장사상충 예방약 얼마나 자주 먹여?", candidates=candidates, pending=None)
        is False
    )


@pytest.mark.parametrize(
    "query",
    [
        "그거 얼마나 자주 해?",
        "아까 말한 거 다시 설명해줘",
        "아니 산책 말고 밥",
        "그러니까 발을 저는 이유가 뭐냐고",
        "그니까 그걸 네가 물어봐야지",
    ],
)
def test_context_dependent_markers_need_resolution(query: str) -> None:
    candidates = [_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")]
    assert needs_resolution(query=query, candidates=candidates, pending=None) is True


def test_pending_clarification_always_needs_resolution() -> None:
    """수용 케이스 5 의 앞 절반 — 되묻기가 대기 중이면 표지가 없어도 이어야 한다."""
    pending = PendingClarification(
        turn_id=uuid.uuid4(),
        question="식욕과 활력 중 어느 쪽이 달라 보이나요?",
        missing_axes=[ObservationAxis.APPETITE, ObservationAxis.ENERGY],
    )
    assert needs_resolution(query="밥은 먹는데 계속 누워 있어", candidates=(), pending=pending) is True


def test_context_ask_is_not_an_observation_ask() -> None:
    """PR 본문 ④ — 축이 비면 후속 답변을 축에 묶지 않는다."""
    context_ask = PendingClarification(
        turn_id=uuid.uuid4(),
        question="이전 대화에서 어떤 내용인지 확인하기 어렵습니다.",
        missing=["observation"],
        missing_axes=[],
    )
    observation_ask = PendingClarification(
        turn_id=uuid.uuid4(),
        question="식욕과 활력 중 어느 쪽이 달라 보이나요?",
        missing=["observation"],
        missing_axes=[ObservationAxis.APPETITE],
    )
    # `missing` 은 둘 다 같다 — 가르는 것은 축뿐이다.
    assert context_ask.missing == observation_ask.missing
    assert context_ask.is_observation_ask is False
    assert observation_ask.is_observation_ask is True


def test_new_turn_carries_the_query_verbatim_and_nothing_else() -> None:
    resolved = new_turn("강아지 사료 추천해줘")
    assert resolved.relation is TurnRelation.NEW
    assert resolved.current_query == "강아지 사료 추천해줘"
    assert resolved.referenced_turn_id is None
    assert resolved.pending_clarification_id is None
    assert resolved.standalone_query is None
    assert resolved.context_used == []


def test_resolved_turn_rejects_two_anchors() -> None:
    """referenced_turn_id 와 pending_clarification_id 는 둘 중 하나만."""
    with pytest.raises(ValueError):
        ResolvedTurn(
            relation=TurnRelation.FOLLOW_UP,
            current_query="그거 얼마나 자주 해?",
            referenced_turn_id=uuid.uuid4(),
            pending_clarification_id=uuid.uuid4(),
            resolution_confidence=0.9,
        )
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && uv run pytest tests/test_orchestration_turn_resolver.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'daengs_backend.orchestration.resolver'`

- [ ] **Step 3: 최소 구현**

```python
# backend/src/daengs_backend/orchestration/resolver.py
"""현재 발화를 앞 요청에 잇는 자리 (#416).

**이력 원문은 이 파일에서 멈춥니다.** 아래로 내려가는 것은 `ConversationContext` 뿐이고,
라우터도 capability 도 후보 turn 을 못 봅니다 — 관계 판정이 프롬프트 안에 묻히면 실패가
관측되지 않기 때문입니다(스펙 §2).

`emergency.py` · `semantic.py` 와 같은 층의 형제입니다. 순서는 **응급 → 결정론 → 여기 →
시맨틱 라우터**이고, 응급이 앞인 것은 의도입니다: 응급 경계는 현재 사용자 원문을 직접
검사해야 하고 이 파일의 결과가 그것을 약하게 만들 수 없습니다(스펙 ⑦-4).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field, model_validator

from daengs_backend.orchestration.contracts import ContractModel, ObservationAxis

#: 후보로 쓰는 완료 turn 쌍의 수. 가장 긴 수용 케이스가 요구하는 최소가 3이다.
MAX_CANDIDATE_PAIRS = 3
#: 후보의 assistant 원문 상한. DB 상한이 8,000자라 안 자르면 통째로 프롬프트에 온다.
MAX_ASSISTANT_CHARS = 400
#: 후보 블록 전체 상한. 한 쌍의 최대치(2,000+400)가 이 아래라 최신 쌍은 늘 남는다.
MAX_CANDIDATE_BLOCK_CHARS = 3_000
#: 이 아래면 잇지 않고 되묻는다 (수용 케이스 9).
RESOLUTION_CONFIDENCE_FLOOR = 0.6


class TurnRelation(StrEnum):
    """현재 발화가 앞 대화와 맺는 관계.

    **`AssistantStatus` 를 안 넓히는 이유와 같은 이유로 이 열거형은 여기 삽니다** — 공유
    열거형을 넓히면 그것을 열거하는 파일이 전부 범위에 들어옵니다.
    """

    NEW = "NEW"
    FOLLOW_UP = "FOLLOW_UP"
    CORRECTION = "CORRECTION"
    REPEAT = "REPEAT"
    META = "META"


class PriorTurn(ContractModel):
    """후보 한 쌍. `turn_id` 가 있어야 모델이 지목한 것을 행으로 되돌릴 수 있다."""

    turn_id: uuid.UUID
    user: str
    assistant: str


class PendingClarification(ContractModel):
    """대기 중인 되묻기 — 가장 최근 완료 turn 이 `CLARIFY` 일 때만 있다.

    **되묻기는 두 부류다** (PR 본문 ④):

    | 부류 | `missing_axes` | 후속 답변을 어디에 묶나 |
    | --- | --- | --- |
    | 관찰 되묻기 | `["APPETITE", …]` | 그 축에 묶는다 |
    | 맥락 되묻기 | `[]` | 축이 아니라 **가리킨 대상**을 물은 것이다 |

    `missing` 으로는 못 가른다 — General ask 는 늘 `["observation"]` 이다
    (`GENERAL_ASK_MISSING` 하드코딩). 가르는 것은 `missing_axes` 의 비어 있음뿐이고,
    비어 있으면 **"축을 모른다"** 이지 "물은 것이 없다" 가 아니다 — 그래서
    `is_observation_ask` 가 거짓일 때 **축을 지어내면 안 된다.**

    `missing` 을 그래도 들고 다니는 것은 좌표 게이트(`["location.lat"]`)와 General ask 를
    가리기 위해서다 — 그 둘은 계획 시점과 집계 시점이라 출처가 다르다.
    """

    turn_id: uuid.UUID
    question: str
    missing: list[str] = Field(default_factory=list)
    missing_axes: list[ObservationAxis] = Field(default_factory=list)

    @property
    def is_observation_ask(self) -> bool:
        """축이 있으면 관찰 되묻기. 없으면 맥락 되묻기이거나 축을 모르는 것이고, 둘 다
        후속 답변을 축에 묶으면 안 된다."""
        return bool(self.missing_axes)


class ConversationContext(ContractModel):
    """라우터와 선택된 capability 가 보는 **전부**. 이력 원문은 여기 없다."""

    relation: TurnRelation
    referenced_original_request: str | None = None
    standalone_query: str | None = None
    pending_question: str | None = None
    pending_missing_axes: list[ObservationAxis] = Field(default_factory=list)


class ResolvedTurn(ContractModel):
    """판정 결과. `current_query` 는 **절대 대체하지 않는다** — 원문과 출처를 늘 같이 든다."""

    relation: TurnRelation
    current_query: str = Field(min_length=1)
    referenced_turn_id: uuid.UUID | None = None
    pending_clarification_id: uuid.UUID | None = None
    referenced_original_request: str | None = None
    pending_missing_axes: list[ObservationAxis] = Field(default_factory=list)
    #: 모델이 만든 추론용 표현. **사실이 아니다** — 반려견 기록으로 저장하지 않는다.
    standalone_query: str | None = None
    resolution_confidence: float = Field(ge=0.0, le=1.0)
    ambiguity: str | None = None
    context_used: list[uuid.UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def one_anchor_at_most(self) -> ResolvedTurn:
        if self.referenced_turn_id is not None and self.pending_clarification_id is not None:
            raise ValueError("referenced_turn_id and pending_clarification_id are exclusive")
        return self


#: 맥락 의존 신호. 없으면 모델을 안 태운다 — `resolve_emergency_route` ·
#: `resolve_deterministic_route` 가 이미 모델 앞에서 하는 것과 같은 규칙 기반 선별이다.
_CONTEXT_MARKERS = re.compile(
    r"그거|그걸|그것|저거|저걸|걔|아까|방금|말한\s*거|"          # 지시어
    r"아니(?![요라])|말고|가\s*아니라|이\s*아니라|"                # 정정
    r"그러니까|그니까|다시|또|했잖아|물어봤|"                      # 반복
    r"물어봐야|왜\s*안|안\s*물어"                                   # 메타
)


def needs_resolution(
    *,
    query: str,
    candidates: Sequence[PriorTurn],
    pending: PendingClarification | None,
) -> bool:
    """모델을 태울 값이 있나.

    대기 중인 되묻기가 있으면 표지와 무관하게 참이다 — `"밥은 먹는데 계속 누워 있어"` 에는
    지시어가 없지만 그것이 앞 질문의 답이기 때문이다(수용 케이스 5).
    """
    if pending is not None:
        return True
    if not candidates:
        return False
    return _CONTEXT_MARKERS.search(query) is not None


def new_turn(query: str) -> ResolvedTurn:
    """fast path 의 결과. 아무것도 안 잇고, 아무것도 안 넘긴다."""
    return ResolvedTurn(
        relation=TurnRelation.NEW, current_query=query, resolution_confidence=1.0
    )
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && uv run pytest tests/test_orchestration_turn_resolver.py -q`
Expected: PASS (10 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/src/daengs_backend/orchestration/resolver.py backend/tests/test_orchestration_turn_resolver.py
git commit -m "feat: Turn Resolver 계약과 NEW fast path — 맥락 신호가 없으면 모델을 안 태운다"
```

---

### Task 2: 후보 블록 — 길이 한도와 렌더

**Files:**
- Modify: `backend/src/daengs_backend/orchestration/resolver.py`
- Test: `backend/tests/test_orchestration_turn_resolver.py`

**Interfaces:**
- Consumes: Task 1 의 `PriorTurn`, `MAX_ASSISTANT_CHARS`, `MAX_CANDIDATE_BLOCK_CHARS`
- Produces: `truncate_assistant(text) -> str`, `fit_candidates(turns) -> list[PriorTurn]`, `build_candidate_block(turns) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
from daengs_backend.orchestration.resolver import (
    MAX_ASSISTANT_CHARS,
    build_candidate_block,
    fit_candidates,
    truncate_assistant,
)


def test_assistant_text_is_truncated_with_an_ellipsis() -> None:
    long = "가" * 500
    out = truncate_assistant(long)
    assert len(out) == MAX_ASSISTANT_CHARS + 1
    assert out.endswith("…")


def test_short_assistant_text_is_untouched() -> None:
    assert truncate_assistant("네, 맞습니다.") == "네, 맞습니다."


def test_oldest_pairs_are_dropped_first_when_the_block_is_too_long() -> None:
    turns = [_turn("질" + "문" * 1_500, "답" * 400) for _ in range(3)]
    fitted = fit_candidates(turns)
    assert len(fitted) < 3
    assert fitted[-1] is turns[-1]  # 최신 쌍은 무조건 남는다


def test_the_newest_pair_always_survives() -> None:
    """한 쌍의 최대치(2,000+400)가 블록 상한 아래라 이 규칙은 늘 만족 가능하다."""
    turns = [_turn("질" * 2_000, "답" * 400)]
    assert fit_candidates(turns) == turns


def test_block_numbers_pairs_oldest_first_for_reference() -> None:
    turns = [_turn("첫 질문", "첫 답"), _turn("둘째 질문", "둘째 답")]
    block = build_candidate_block(turns)
    assert "U1: 첫 질문" in block
    assert "A1: 첫 답" in block
    assert "U2: 둘째 질문" in block
    assert block.index("U1:") < block.index("U2:")


def test_empty_candidates_render_to_an_empty_block() -> None:
    assert build_candidate_block([]) == ""
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && uv run pytest tests/test_orchestration_turn_resolver.py -q -k "truncate or fit or block"`
Expected: FAIL — `ImportError: cannot import name 'truncate_assistant'`

- [ ] **Step 3: 최소 구현** (`resolver.py` 에 추가)

```python
def truncate_assistant(text: str) -> str:
    """`MAX_ASSISTANT_CHARS` 에서 자르고 `…` 를 붙인다. DB 상한은 8,000자다."""
    if len(text) <= MAX_ASSISTANT_CHARS:
        return text
    return text[:MAX_ASSISTANT_CHARS] + "…"


def fit_candidates(turns: Sequence[PriorTurn]) -> list[PriorTurn]:
    """블록 상한에 맞게 **오래된 쌍부터** 버린다. 최신 쌍은 무조건 남는다.

    한 쌍의 최대치가 user 2,000자 + assistant 400자 = 2,400자로 `MAX_CANDIDATE_BLOCK_CHARS`
    아래이므로 이 규칙은 늘 만족 가능하다. 세 숫자는 같이 움직여야 한다.
    """
    kept: list[PriorTurn] = []
    total = 0
    for turn in reversed(list(turns)):
        size = len(turn.user) + len(truncate_assistant(turn.assistant))
        if kept and total + size > MAX_CANDIDATE_BLOCK_CHARS:
            break
        kept.append(turn)
        total += size
    return list(reversed(kept))


def build_candidate_block(turns: Sequence[PriorTurn]) -> str:
    """오래된 것부터, 한 줄에 한 발화, 번호를 붙여서.

    **번호가 `referenced_turn_id` 의 근거다** — 모델이 "U1 을 가리킨다" 고 말할 수 있어야
    서버가 그것을 실제 turn id 로 되돌린다. JSON 이 아닌 것은 따옴표·이스케이프로 토큰이
    늘기 때문이다.
    """
    lines: list[str] = []
    for index, turn in enumerate(turns, start=1):
        lines.append(f"U{index}: {turn.user}")
        lines.append(f"A{index}: {truncate_assistant(turn.assistant)}")
    return "\n".join(lines)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && uv run pytest tests/test_orchestration_turn_resolver.py -q`
Expected: PASS (16 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/src/daengs_backend/orchestration/resolver.py backend/tests/test_orchestration_turn_resolver.py
git commit -m "feat: 후보 블록 — 400자 절단, 오래된 쌍부터 버리기, 번호로 turn id 되돌리기"
```

---

### Task 3: 프롬프트와 출력 검증

**Files:**
- Modify: `backend/src/daengs_backend/orchestration/resolver.py`
- Test: `backend/tests/test_orchestration_turn_resolver.py`

**Interfaces:**
- Consumes: Task 1·2 전부
- Produces: `TURN_RESOLVER_PROMPT_VERSION = "turn-resolver-ko-v1"`, `TURN_RESOLVER_MODEL_ID = "gemini-3.1-flash-lite"`, `build_turn_resolver_prompt(*, query, candidates, pending) -> str`, `validate_resolved_turn(raw, *, query, candidates, pending) -> ResolvedTurn | None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
from daengs_backend.orchestration.resolver import (
    TURN_RESOLVER_PROMPT_VERSION,
    build_turn_resolver_prompt,
    validate_resolved_turn,
)


def test_current_query_is_last_in_the_prompt() -> None:
    """Place 실측(2026-09-10): 문맥 뒤에 최신 질의를 두면 최신 요청을 놓치는 퇴행이 사라진다."""
    turns = [_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")]
    prompt = build_turn_resolver_prompt(query="그거 얼마나 자주 해?", candidates=turns, pending=None)
    assert prompt.index("CANDIDATE_TURNS:") < prompt.index("CURRENT_QUERY:")
    assert prompt.rstrip().endswith("CURRENT_QUERY: 그거 얼마나 자주 해?")
    assert TURN_RESOLVER_PROMPT_VERSION in prompt


def test_pending_clarification_block_carries_axes_as_asked_not_observed() -> None:
    pending = PendingClarification(
        turn_id=uuid.uuid4(),
        question="식욕과 활력 중 어느 쪽이 달라 보이나요?",
        missing_axes=[ObservationAxis.APPETITE],
    )
    prompt = build_turn_resolver_prompt(query="밥은 먹는데 계속 누워 있어", candidates=(), pending=pending)
    assert "PENDING_CLARIFICATION:" in prompt
    assert "APPETITE" in prompt
    # 물은 항목이지 관찰된 사실이 아니라는 것을 프롬프트가 직접 말한다.
    assert "asked" in prompt


def test_model_may_not_reference_a_turn_outside_the_candidates() -> None:
    turns = [_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")]
    raw = {
        "relation": "FOLLOW_UP",
        "referenced_index": 9,
        "standalone_query": "사료를 얼마나 자주 줘?",
        "resolution_confidence": 0.9,
    }
    assert validate_resolved_turn(raw, query="그거 얼마나 자주 해?", candidates=turns, pending=None) is None


def test_referenced_index_becomes_the_real_turn_id() -> None:
    turns = [_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")]
    raw = {
        "relation": "FOLLOW_UP",
        "referenced_index": 1,
        "standalone_query": "사료를 얼마나 자주 줘?",
        "resolution_confidence": 0.9,
    }
    resolved = validate_resolved_turn(
        raw, query="그거 얼마나 자주 해?", candidates=turns, pending=None
    )
    assert resolved is not None
    assert resolved.referenced_turn_id == turns[0].turn_id
    assert resolved.referenced_original_request == "사료 추천해줘"
    assert resolved.context_used == [turns[0].turn_id]
    # 원문은 모델이 만든 표현으로 대체되지 않는다.
    assert resolved.current_query == "그거 얼마나 자주 해?"


def test_malformed_output_is_rejected_without_surfacing_it() -> None:
    assert validate_resolved_turn("not json", query="아무 말", candidates=(), pending=None) is None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && uv run pytest tests/test_orchestration_turn_resolver.py -q -k "prompt or validate or reference or malformed"`
Expected: FAIL — `ImportError: cannot import name 'build_turn_resolver_prompt'`

- [ ] **Step 3: 최소 구현** (`resolver.py` 에 추가)

```python
import json
from typing import Any

from pydantic import BaseModel, ValidationError

TURN_RESOLVER_MODEL_ID = "gemini-3.1-flash-lite"
TURN_RESOLVER_PROMPT_VERSION = "turn-resolver-ko-v1"

_POLICY = """You classify how the owner's current message relates to the conversation so far. You do not answer it.

Return exactly one JSON object conforming to the supplied schema.

relation is one of:
- NEW: the message stands on its own. Choose this whenever the earlier turns are about a different topic. An unrelated earlier turn must not drag the current message toward it.
- FOLLOW_UP: the message continues an earlier request, or answers PENDING_CLARIFICATION.
- CORRECTION: the owner is correcting the premise of an earlier request ("아니, 산책 말고 밥").
- REPEAT: the owner is asking again for something an earlier turn failed to deliver.
- META: the message is about this conversation itself — a complaint, or telling you what you should have asked. It is not off-topic.

referenced_index names the candidate pair the message attaches to, as the number in U1/A1. Use null when relation is NEW or when the message answers PENDING_CLARIFICATION instead.

standalone_query is the current message rewritten so it can be read alone. It is a working restatement, never a fact about the owner or the dog. Leave it null when relation is NEW.

resolution_confidence is how sure you are of the attachment, 0.0 to 1.0. When the message points at something you cannot identify among the candidates, set a low confidence and say what is unclear in ambiguity. Do not guess an attachment.

PENDING_CLARIFICATION.asked_axes are the items the assistant asked about. They are NOT observations about the dog and NOT facts. An empty list means the axes are unknown, not that nothing was asked."""


class _RawResolution(BaseModel):
    relation: TurnRelation
    referenced_index: int | None = None
    standalone_query: str | None = None
    resolution_confidence: float = Field(ge=0.0, le=1.0)
    ambiguity: str | None = None


def build_turn_resolver_prompt(
    *,
    query: str,
    candidates: Sequence[PriorTurn],
    pending: PendingClarification | None,
) -> str:
    """`CURRENT_QUERY:` 가 **맨 뒤**다 — Place 실측이 그 배치를 요구한다(스펙 §2)."""
    if not query.strip():
        raise ValueError("query must not be blank")
    schema = json.dumps(_RawResolution.model_json_schema(), ensure_ascii=False, sort_keys=True)
    parts = [
        f"PROMPT_VERSION: {TURN_RESOLVER_PROMPT_VERSION}",
        _POLICY,
        f"RESOLUTION_JSON_SCHEMA:\n{schema}",
    ]
    block = build_candidate_block(candidates)
    if block:
        parts.append(f"CANDIDATE_TURNS:\n{block}")
    if pending is not None:
        asked = json.dumps(
            {"question": pending.question, "asked_axes": [str(a) for a in pending.missing_axes]},
            ensure_ascii=False,
            sort_keys=True,
        )
        parts.append(f"PENDING_CLARIFICATION: {asked}")
    parts.append(f"CURRENT_QUERY: {query}")
    return "\n\n".join(parts) + "\n"


def validate_resolved_turn(
    raw: object,
    *,
    query: str,
    candidates: Sequence[PriorTurn],
    pending: PendingClarification | None,
) -> ResolvedTurn | None:
    """스키마 검증 + **후보 밖 지목 거부**. 잘못된 원본 출력은 밖으로 안 내보낸다."""
    parsed: object = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
    try:
        decision = _RawResolution.model_validate(parsed)
    except (ValidationError, ValueError, TypeError):
        return None

    referenced: PriorTurn | None = None
    if decision.referenced_index is not None:
        index = decision.referenced_index
        if not 1 <= index <= len(candidates):
            return None
        referenced = candidates[index - 1]

    anchored_to_pending = pending is not None and referenced is None
    return ResolvedTurn(
        relation=decision.relation,
        current_query=query,
        referenced_turn_id=referenced.turn_id if referenced else None,
        pending_clarification_id=(
            pending.turn_id if anchored_to_pending and decision.relation is not TurnRelation.NEW
            else None
        ),
        referenced_original_request=referenced.user if referenced else None,
        pending_missing_axes=(
            list(pending.missing_axes) if anchored_to_pending and pending else []
        ),
        standalone_query=decision.standalone_query,
        resolution_confidence=decision.resolution_confidence,
        ambiguity=decision.ambiguity,
        context_used=[referenced.turn_id] if referenced else [],
    )
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && uv run pytest tests/test_orchestration_turn_resolver.py -q`
Expected: PASS (21 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/src/daengs_backend/orchestration/resolver.py backend/tests/test_orchestration_turn_resolver.py
git commit -m "feat: Turn Resolver 프롬프트와 출력 검증 — CURRENT_QUERY 를 맨 뒤에, 후보 밖 지목은 거부"
```

---

### Task 4: `GeminiTurnResolver` — 제공자 호출과 실패 계약

**Files:**
- Modify: `backend/src/daengs_backend/orchestration/resolver.py`
- Test: `backend/tests/test_orchestration_turn_resolver.py`

**Interfaces:**
- Consumes: Task 3 전부
- Produces: `TurnResolutionError`, `GeminiTurnResolver(generate=None)` with `async def resolve(*, query, candidates, pending) -> ResolvedTurn`

`semantic.GeminiSemanticRouter` 를 그대로 따라간다 — 생성자가 `generate` 콜러블을 받아 테스트가 갈아끼운다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
import pytest

from daengs_backend.orchestration.resolver import GeminiTurnResolver, TurnResolutionError


@pytest.mark.anyio
async def test_resolver_skips_the_model_entirely_on_the_fast_path() -> None:
    """수용 케이스 1 — 모델 호출 0회."""
    calls: list[str] = []

    async def _generate(prompt: str) -> object:
        calls.append(prompt)
        raise AssertionError("fast path must not call the model")

    resolver = GeminiTurnResolver(generate=_generate)
    resolved = await resolver.resolve(query="사료 추천해줘", candidates=(), pending=None)
    assert resolved.relation is TurnRelation.NEW
    assert calls == []


@pytest.mark.anyio
async def test_resolver_returns_the_validated_decision() -> None:
    turns = [_turn("심장사상충 예방약 먹여야 해?", "네, 보통 한 달에 한 번 투여합니다.")]

    async def _generate(prompt: str) -> object:
        return {
            "relation": "FOLLOW_UP",
            "referenced_index": 1,
            "standalone_query": "심장사상충 예방약을 얼마나 자주 먹여?",
            "resolution_confidence": 0.92,
        }

    resolver = GeminiTurnResolver(generate=_generate)
    resolved = await resolver.resolve(query="그거 얼마나 자주 해?", candidates=turns, pending=None)
    assert resolved.relation is TurnRelation.FOLLOW_UP
    assert resolved.referenced_turn_id == turns[0].turn_id


@pytest.mark.anyio
async def test_provider_failure_raises_the_contracted_error() -> None:
    async def _generate(prompt: str) -> object:
        raise TimeoutError("provider down")

    resolver = GeminiTurnResolver(generate=_generate)
    with pytest.raises(TurnResolutionError):
        await resolver.resolve(query="그거 얼마나 자주 해?", candidates=[_turn("a", "b")], pending=None)


@pytest.mark.anyio
async def test_unparseable_output_raises_the_contracted_error() -> None:
    async def _generate(prompt: str) -> object:
        return "{nope"

    resolver = GeminiTurnResolver(generate=_generate)
    with pytest.raises(TurnResolutionError):
        await resolver.resolve(query="그거 얼마나 자주 해?", candidates=[_turn("a", "b")], pending=None)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && uv run pytest tests/test_orchestration_turn_resolver.py -q -k "resolver"`
Expected: FAIL — `ImportError: cannot import name 'GeminiTurnResolver'`

- [ ] **Step 3: 최소 구현**

`semantic.py` 의 `_gemini_client` · `_generate_with_gemini` · `_traced_generate` 를 읽고 같은 모양으로 쓴다. 생성 설정은 `semantic.ROUTER_TEMPERATURE`(0.0) · `ROUTER_CANDIDATE_COUNT`(1) 를 **import 해서** 쓴다 — 숫자를 따로 적으면 "같은 설정으로 쟀다" 를 코드가 증명하지 못한다.

```python
from collections.abc import Awaitable, Callable
import logging

LOGGER = logging.getLogger(__name__)


class TurnResolutionError(Exception):
    """제공자 실패 또는 스키마 실패. 원본 출력은 절대 밖으로 안 나간다."""


class GeminiTurnResolver:
    def __init__(self, generate: Callable[[str], Awaitable[object]] | None = None) -> None:
        self._generate = generate or _generate_with_gemini

    async def resolve(
        self,
        *,
        query: str,
        candidates: Sequence[PriorTurn],
        pending: PendingClarification | None,
    ) -> ResolvedTurn:
        fitted = fit_candidates(candidates)
        if not needs_resolution(query=query, candidates=fitted, pending=pending):
            return new_turn(query)
        prompt = build_turn_resolver_prompt(query=query, candidates=fitted, pending=pending)
        try:
            raw = await self._generate(prompt)
        except Exception as exc:  # noqa: BLE001 — 제공자 예외를 하나의 계약 오류로 좁힌다
            raise TurnResolutionError("turn resolution provider failed") from exc
        resolved = validate_resolved_turn(raw, query=query, candidates=fitted, pending=pending)
        if resolved is None:
            raise TurnResolutionError("turn resolution output failed schema validation")
        return resolved
```

`_generate_with_gemini` 는 `semantic._generate_with_gemini` 와 같은 구조로 쓰되 `response_json_schema=_RawResolution.model_json_schema()` 와 `TURN_RESOLVER_MODEL_ID` 를 쓴다.

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && uv run pytest tests/test_orchestration_turn_resolver.py -q`
Expected: PASS (25 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/src/daengs_backend/orchestration/resolver.py backend/tests/test_orchestration_turn_resolver.py
git commit -m "feat: GeminiTurnResolver — fast path 는 모델을 안 부르고, 실패는 하나의 계약 오류로"
```

---

### Task 5: 오케스트레이션 배선 — 응급이 먼저, 낮은 확신은 **잇지 않는다**

**Files:**
- Modify: `backend/src/daengs_backend/orchestration/service.py:69-205`
- Modify: `backend/src/daengs_backend/orchestration/contracts.py` (`GeneralPayload` 에 `conversation` 한 칸)
- Modify: `backend/src/daengs_backend/orchestration/planner.py` (`assemble_route_plan` · `_payload_for`)
- Test: `backend/tests/test_orchestration_turn_resolver_wiring.py`

**Interfaces:**
- Consumes: Task 4 의 `GeminiTurnResolver`, `ResolvedTurn`, `RESOLUTION_CONFIDENCE_FLOOR`
- Produces: `AssistantOrchestrationService.run(..., prior_turns: Sequence[PriorTurn] = (), pending_clarification: PendingClarification | None = None)`, `GeneralPayload.conversation: ConversationContext | None`, `assemble_route_plan(..., resolved: ResolvedTurn | None = None)`

- [ ] **Step 1: 실패하는 테스트를 쓴다 — 수용 케이스 8·9**

```python
# backend/tests/test_orchestration_turn_resolver_wiring.py
"""Resolver 가 오케스트레이션의 어느 자리에 서는가.

수용 케이스 8(응급 우선)과 9(확신이 낮으면 되묻기)가 여기서 걸린다.
"""
import uuid

import pytest

from daengs_backend.orchestration.contracts import AssistantStatus, PrincipalContext
from daengs_backend.orchestration.resolver import PriorTurn, ResolvedTurn, TurnRelation


def _turn(user: str, assistant: str) -> PriorTurn:
    return PriorTurn(turn_id=uuid.uuid4(), user=user, assistant=assistant)


@pytest.mark.anyio
async def test_emergency_wins_before_the_resolver_runs(monkeypatch) -> None:
    """수용 케이스 8 — 과거 맥락과 무관하게 현재 발화의 응급 신호가 이긴다."""
    calls: list[str] = []

    class _Spy:
        async def resolve(self, **kwargs: object) -> ResolvedTurn:
            calls.append("resolved")
            raise AssertionError("emergency must short-circuit before resolution")

    service = _service_with(resolver=_Spy())
    response = await service.run(
        query="강아지가 초콜릿을 먹었어 지금 어떡해",
        principal=PrincipalContext(kind="app_user", permissions=[]),
        prior_turns=[_turn("산책 코스 추천해줘", "근처 공원을 추천합니다.")],
    )
    assert calls == []
    assert response.status is not AssistantStatus.FAILED


@pytest.mark.anyio
async def test_low_confidence_asks_instead_of_guessing() -> None:
    """수용 케이스 9 — 확정 못 하는 '그거' 는 임의로 잇지 않는다.

    **Resolver 는 CLARIFY 를 만들지 않는다** (PR 본문 ⑦ 의 답). 붙임을 버리고 relation=NEW
    로 내려보내면 General 의 기존 ask 경로가 그대로 되묻는다 — after_v2 랩에서 이미
    그 문장이 나오고 있다. 생산자는 둘로 유지된다.
    """

    class _Unsure:
        async def resolve(self, **kwargs: object) -> ResolvedTurn:
            return ResolvedTurn(
                relation=TurnRelation.FOLLOW_UP,
                current_query="그거 얼마나 자주 해?",
                resolution_confidence=0.2,
                ambiguity="어느 것을 가리키는지 후보에서 못 찾았습니다",
            )

    captured: dict = {}
    service = _service_with(resolver=_Unsure(), general_sink=captured)
    await service.run(
        query="그거 얼마나 자주 해?",
        principal=PrincipalContext(kind="app_user", permissions=[]),
        prior_turns=[_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")],
    )
    # 붙임이 버려져 아래로 아무것도 안 간다 — General 이 오늘처럼 되묻는다.
    assert captured["payload"].conversation is None


@pytest.mark.anyio
async def test_resolution_failure_degrades_to_todays_behaviour() -> None:
    """Resolver 가 죽어도 답은 나간다 — 이력 기능이 없던 때와 같게 돈다."""

    class _Broken:
        async def resolve(self, **kwargs: object) -> ResolvedTurn:
            from daengs_backend.orchestration.resolver import TurnResolutionError

            raise TurnResolutionError("down")

    service = _service_with(resolver=_Broken())
    response = await service.run(
        query="그거 얼마나 자주 해?",
        principal=PrincipalContext(kind="app_user", permissions=[]),
        prior_turns=[_turn("사료 추천해줘", "저알레르기 사료를 고려해 보세요.")],
    )
    assert response.status is not AssistantStatus.FAILED
```

`_service_with` 는 `tests/test_orchestration_semantic_router.py` 가 오케스트레이터를 조립하는 방식을 그대로 따른다 — 그 파일을 먼저 읽고 같은 헬퍼 모양을 쓴다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && uv run pytest tests/test_orchestration_turn_resolver_wiring.py -q`
Expected: FAIL — `TypeError: run() got an unexpected keyword argument 'prior_turns'`

- [ ] **Step 3: 구현**

`service._plan_and_execute` 에 한 단계를 넣는다. **순서가 계약이다:**

```python
# 응급은 라우터보다 앞이다 — 모델을 안 태우고, 배타로 끝낸다.
route_plan = resolve_emergency_route(...)
if route_plan is None:
    route_plan = resolve_deterministic_route(...)

# ── Turn Resolver (#416). 응급·결정론 **뒤**, 시맨틱 라우터 **앞**.
# 응급이 앞인 것은 의도다: 응급 경계는 현재 사용자 원문을 직접 검사해야 하고, 이
# 판정이 그것을 약하게 만들 수 없다. 결정론이 앞인 것은 명시 신호가 이미 답이기
# 때문이다 — 관계를 물을 이유가 없다.
resolved: ResolvedTurn | None = None
if route_plan is None:
    try:
        resolved = await self._turn_resolver.resolve(
            query=query, candidates=prior_turns, pending=pending_clarification
        )
    except TurnResolutionError as exc:
        # **답은 나간다.** 이력 기제가 없던 때와 같게 도는 것이 실패 모드다 —
        # 대화 이어짐이 안 되는 것이 답이 안 나오는 것보다 낫다. 질문 원문은 안 남긴다(D-037).
        LOGGER.warning("턴 해소 실패 request_id=%s: %s (원인: %r)", rid, exc, exc.__cause__)
        resolved = None
    else:
        if (
            resolved.relation is TurnRelation.NEW
            or resolved.resolution_confidence < RESOLUTION_CONFIDENCE_FLOOR
        ):
            # **CLARIFY 생산자를 셋으로 안 만든다** (PR 본문 ⑦ 의 답).
            #
            # 확신이 낮으면 붙임을 **버리기만** 한다. 그러면 General 의 기존 ask 경로가
            # 오늘 하던 대로 되묻는다 — 그 프롬프트에 이미 "an unresolved 그거 or 아까
            # 말한 거 ... say only that you cannot see what it refers to" 가 있고,
            # after_v2 랩이 `cq_pronoun_geugeo_01` · `cq_pronoun_akka_01` 에서 실제로 그
            # 문장을 내고 있다. 되묻기는 **이미 돌고 있는 것**이라 새로 만들 것이 없다.
            #
            # 여기서 세 번째 생산자를 만들면 `contracts.md` §2 와 진리표를 또 고쳐야 하고,
            # 그 값을 치를 근거가 아직 없다.
            resolved = None  # 넘길 것이 없으면 넘기지 않는다 — 바이트 동일 보장
```

그 뒤 `self._semantic_router.select(query=query, context=structured_context, resolved=resolved)` 와 `assemble_route_plan(..., resolved=resolved)` 로 내려보낸다.

`contracts.py`:

```python
class GeneralPayload(ContractModel):
    ...
    #: 대화 맥락 (#416). **이력 원문이 아니다** — Turn Resolver 가 만든 제한된 구조화
    #: 컨텍스트이고, `relation=NEW` 면 `None` 이라 프롬프트가 오늘과 바이트 동일하다.
    conversation: ConversationContext | None = None
```

`planner._payload_for(capability, *, query, context, resolved=None)` 의 `_GENERAL` 가지에서만 `conversation=_conversation_of(resolved)` 를 채운다. 다른 능력은 안 받는다.

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && uv run pytest tests/test_orchestration_turn_resolver_wiring.py tests/test_orchestration_semantic_router.py tests/test_orchestration_general_fallback.py -q`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add backend/src/daengs_backend/orchestration/ backend/tests/test_orchestration_turn_resolver_wiring.py
git commit -m "feat: Resolver 를 응급 뒤·라우터 앞에 세운다 — 확신이 낮으면 잇지 않고 되묻는다"
```

---

### Task 6: 라우터·General 프롬프트에 구조화 컨텍스트

**Files:**
- Modify: `backend/src/daengs_backend/orchestration/semantic.py:247-260`
- Modify: `backend/src/daengs_backend/orchestration/adapters/general.py:145-207`
- Test: `backend/tests/test_orchestration_turn_resolver_prompts.py`

**Interfaces:**
- Consumes: Task 5 의 `ConversationContext`
- Produces: `semantic.RESOLVED_PROMPT_VERSION = "semantic-router-ko-v10-resolved"`, `general.general_prompt_version(payload) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다 — 수용 케이스 1 의 코드 형태**

```python
# backend/tests/test_orchestration_turn_resolver_prompts.py
from daengs_backend.orchestration.adapters.general import (
    GENERAL_PROMPT_VERSION,
    build_general_prompt,
    general_prompt_version,
)
from daengs_backend.orchestration.contracts import GeneralPayload
from daengs_backend.orchestration.resolver import ConversationContext, TurnRelation
from daengs_backend.orchestration.semantic import (
    PROMPT_VERSION,
    RESOLVED_PROMPT_VERSION,
    build_semantic_router_prompt,
)


def test_router_prompt_is_byte_identical_without_a_resolution() -> None:
    """수용 케이스 1 — 이력이 안 닿는 요청은 오늘과 **같은 문자열**이다."""
    context = {"source": "app", "active_dog_id": "d1"}
    assert build_semantic_router_prompt(query="사료 추천해줘", context=context) == (
        build_semantic_router_prompt(query="사료 추천해줘", context=context, resolved=None)
    )
    assert PROMPT_VERSION in build_semantic_router_prompt(query="사료 추천해줘", context=context)


def test_general_prompt_is_byte_identical_without_a_conversation() -> None:
    payload = GeneralPayload(question="사료 추천해줘")
    assert build_general_prompt(payload) == build_general_prompt(
        GeneralPayload(question="사료 추천해줘", conversation=None)
    )
    assert general_prompt_version(payload) == GENERAL_PROMPT_VERSION


def test_resolution_goes_before_the_user_query_and_flips_the_version() -> None:
    context = {"source": "app"}
    resolved_ctx = ConversationContext(
        relation=TurnRelation.FOLLOW_UP, referenced_original_request="사료 추천해줘"
    )
    prompt = build_semantic_router_prompt(
        query="그거 얼마나 자주 해?", context=context, resolved=resolved_ctx
    )
    assert prompt.index("CONVERSATION:") < prompt.index("USER_QUERY:")
    assert prompt.rstrip().endswith("USER_QUERY: 그거 얼마나 자주 해?")
    assert RESOLVED_PROMPT_VERSION in prompt


def test_general_version_gets_a_conv_suffix_when_conversation_rides_along() -> None:
    payload = GeneralPayload(
        question="그거 얼마나 자주 해?",
        conversation=ConversationContext(relation=TurnRelation.FOLLOW_UP),
    )
    assert general_prompt_version(payload) == f"{GENERAL_PROMPT_VERSION}-conv"
    assert "CONVERSATION:" in build_general_prompt(payload)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && uv run pytest tests/test_orchestration_turn_resolver_prompts.py -q`
Expected: FAIL — `ImportError: cannot import name 'RESOLVED_PROMPT_VERSION'`

- [ ] **Step 3: 구현**

`semantic.py` — `PROMPT_VERSION` 은 **그대로 두고** 옆에 하나 더 둔다:

```python
#: 대화 맥락이 실린 프롬프트의 핀 (#416). `PROMPT_VERSION` 을 안 올리는 것은 의도다 —
#: 맥락이 없을 때의 몸이 한 글자도 안 바뀌므로, 같은 상수가 두 몸을 가리키면 랩 헤더의
#: 핀이 거짓말을 한다. `general.py` 가 네 조합에 네 상수를 둔 것과 같은 이유다.
RESOLVED_PROMPT_VERSION = "semantic-router-ko-v10-resolved"
```

`build_semantic_router_prompt(*, query, context, resolved=None)` 은 `resolved` 가 `None` 이면 **지금 코드 경로를 그대로 탄다**(문자열 조립을 재구성하지 않는다). 있을 때만 버전을 바꾸고 `CONVERSATION:` 한 줄을 `USER_QUERY:` 앞에 넣는다. `select(*, query, context, resolved=None)` 도 같이 넓힌다.

`general.py` — `general_prompt_version(payload)` 를 새로 뽑고, `build_general_prompt` 의 **두 갈래 모두** `payload.conversation` 이 있을 때만 `CONVERSATION:` 줄을 `USER_QUERY:` 앞에 더한다. `conversation is None` 인 기존 네 조합은 리터럴이 그대로다.

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && uv run pytest tests/test_orchestration_turn_resolver_prompts.py tests/test_orchestration_ask_mode.py tests/test_assistant_care_log.py tests/test_assistant_vet_spend.py -q`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add backend/src/daengs_backend/orchestration/ backend/tests/test_orchestration_turn_resolver_prompts.py
git commit -m "feat: 구조화 컨텍스트를 USER_QUERY 앞에 — 없을 때의 프롬프트는 한 글자도 안 바뀐다"
```

---

### Task 7: chat 서비스 배선 — 후보와 대기 되묻기를 예약 TX 에서 읽는다

**Files:**
- Modify: `backend/src/daengs_backend/repositories/chat.py`
- Modify: `backend/src/daengs_backend/services/chat.py:573-632`
- Modify: `backend/src/daengs_backend/routers/assistant.py:285-300`
- Test: `backend/tests/test_chat_turn_context.py`

**Interfaces:**
- Consumes: Task 1 의 `PriorTurn` · `PendingClarification`
- Produces: `chat_repo.list_recent_completed_turns(session, session_id, *, limit) -> list[ChatTurn]`, `chat_service.candidates_of(turns) -> list[PriorTurn]`, `chat_service.pending_clarification_of(turns) -> PendingClarification | None`, `Orchestrate` 콜백이 `(active_dog_id, prior_turns, pending)` 를 받음

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# backend/tests/test_chat_turn_context.py
"""저장된 turn 에서 후보와 대기 되묻기를 뽑는 순수 함수들.

DB 가 필요 없다 — `ChatTurn` 을 손으로 만들어 넣는다.
"""
import uuid

from daengs_backend.models.chat import ChatTurn
from daengs_backend.orchestration.contracts import ObservationAxis
from daengs_backend.services.chat import candidates_of, pending_clarification_of


def _completed(user: str, assistant: str, *, status: str = "ANSWERED", clarify=None) -> ChatTurn:
    public: dict = {"status": status, "message": assistant}
    if clarify is not None:
        public["clarify"] = clarify
    return ChatTurn(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        client_message_id=uuid.uuid4(),
        processing_status="completed",
        user_content=user,
        assistant_content=assistant,
        assistant_status=status,
        public_response=public,
    )


def test_candidates_keep_order_and_carry_turn_ids() -> None:
    turns = [_completed("첫 질문", "첫 답"), _completed("둘째 질문", "둘째 답")]
    candidates = candidates_of(turns)
    assert [c.user for c in candidates] == ["첫 질문", "둘째 질문"]
    assert candidates[0].turn_id == turns[0].id


def test_a_trailing_clarify_turn_is_the_pending_clarification() -> None:
    """스펙 ⑥(나) — 「미해결」은 가장 최근 완료 turn 이 CLARIFY 라는 뜻이다."""
    clarify = {
        "question": "식욕과 활력 중 어느 쪽이 달라 보이나요?",
        "missing": ["observation"],
        "missing_axes": ["APPETITE", "ENERGY"],
    }
    turns = [_completed("오늘 건강 어때?", "기록상 …", status="CLARIFY", clarify=clarify)]
    pending = pending_clarification_of(turns)
    assert pending is not None
    assert pending.turn_id == turns[0].id
    assert pending.missing_axes == [ObservationAxis.APPETITE, ObservationAxis.ENERGY]


def test_a_clarify_followed_by_another_turn_is_no_longer_pending() -> None:
    clarify = {"question": "어느 쪽인가요?", "missing": ["observation"], "missing_axes": []}
    turns = [
        _completed("오늘 건강 어때?", "기록상 …", status="CLARIFY", clarify=clarify),
        _completed("밥은 먹어", "그렇군요 …"),
    ]
    assert pending_clarification_of(turns) is None


def test_empty_missing_axes_means_axes_unknown_not_nothing_asked() -> None:
    """`ObservationAxis` docstring 이 #416 을 지목해 경고하는 자리."""
    clarify = {"question": "그거가 무엇을 가리키나요?", "missing": ["reference"], "missing_axes": []}
    turns = [_completed("그거 얼마나 자주 해?", "…", status="CLARIFY", clarify=clarify)]
    pending = pending_clarification_of(turns)
    assert pending is not None          # 되묻기는 분명히 있다
    assert pending.missing_axes == []   # 축만 모른다


def test_no_turns_means_no_pending_and_no_candidates() -> None:
    assert candidates_of([]) == []
    assert pending_clarification_of([]) is None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && uv run pytest tests/test_chat_turn_context.py -q`
Expected: FAIL — `ImportError: cannot import name 'candidates_of'`

- [ ] **Step 3: 구현**

`repositories/chat.py` 에 `list_recent_completed_turns` 를 더한다 — `chat_turns_session_order_idx`(`session_id, created_at, id`)를 그대로 타도록 `ORDER BY created_at DESC, id DESC LIMIT :limit` 한 뒤 뒤집어 돌려준다.

`services/chat.py` 에 순수 함수 둘:

```python
def candidates_of(turns: list[ChatTurn]) -> list[PriorTurn]:
    """완료 turn 을 Resolver 후보로. `failed`·`processing` 은 애초에 안 들어온다."""
    return [
        PriorTurn(turn_id=t.id, user=t.user_content, assistant=t.assistant_content or "")
        for t in turns
    ]


def pending_clarification_of(turns: list[ChatTurn]) -> PendingClarification | None:
    """**가장 최근 완료 turn 이 `CLARIFY` 일 때만** 대기다 (스펙 ⑥ 나).

    뒤에 다른 완료 turn 이 있으면 그 되묻기는 답을 받았거나 버려진 것이고, 어느 쪽이든
    대기가 아니다. 우리 대기는 한 턴짜리라 Place 의 만료·revision 기계가 필요 없다.

    읽는 자리가 `public_response` 인 이유: `public_response_of` 가 `model_dump(mode="json")`
    라 `clarify.question` · `missing` · `missing_axes` 가 통째로 저장돼 있다. 새 칸도 새
    테이블도 필요 없다.

    ⚠ **`public_response["results"]` 를 훑지 않는다.** D-068 이 진리표("CLARIFY = 아무것도
    실행되지 않았음")를 지키려고 되묻기 응답의 `results` 를 **비워서** 내보낸다 — 거기서
    "어느 능력이 돌았나" 를 알아내려 하면 조용히 빈 손이 된다. `clarify` 가 그 답이다.
    """
    if not turns:
        return None
    last = turns[-1]
    if last.assistant_status != "CLARIFY":
        return None
    clarify = (last.public_response or {}).get("clarify")
    if not isinstance(clarify, dict):
        return None
    question = clarify.get("question")
    if not isinstance(question, str) or not question.strip():
        return None
    axes: list[ObservationAxis] = []
    for raw in clarify.get("missing_axes") or []:
        try:
            axes.append(ObservationAxis(raw))
        except ValueError:
            continue  # 목록이 넓어진 뒤의 옛 행 — 축을 모르는 것으로 읽는다
    missing = [m for m in (clarify.get("missing") or []) if isinstance(m, str)]
    return PendingClarification(
        turn_id=last.id, question=question, missing=missing, missing_axes=axes
    )
```

`run_persisted_turn` 은 **이미 잡고 있는 예약 TX 안에서** `list_recent_completed_turns` 를 부르고(새 TX 를 안 연다 — D-048 의 「외부 호출 동안 열린 세션 0개」), `orchestrate(pet_id, candidates, pending)` 로 넘긴다. `Orchestrate` 타입 별칭과 `routers/assistant.py` 의 `orchestrate` 클로저를 같이 넓힌다.

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && uv run pytest tests/test_chat_turn_context.py tests/test_assistant_api.py -q`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add backend/src/daengs_backend/ backend/tests/test_chat_turn_context.py
git commit -m "feat: 후보와 대기 되묻기를 예약 TX 에서 읽어 Resolver 로 넘긴다"
```

---

### Task 8: 수용 케이스 2·3·4·5·6 — 관계 분류의 통합 검증

**Files:**
- Modify: `backend/tests/test_orchestration_turn_resolver_wiring.py`

**Interfaces:**
- Consumes: Task 5·6·7 전부

가짜 resolver 로 관계를 고정한 채 **그 관계가 아래에서 무엇을 바꾸는지**를 본다. 모델 판정 자체의 정확도는 Task 9 의 랩이 잰다 — 두 가지를 한 테스트에서 섞지 않는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
@pytest.mark.anyio
async def test_repeat_does_not_replay_the_same_fixed_refusal() -> None:
    """수용 케이스 4 — REPEAT 판정이 같은 거절 문구를 두 번 내보내지 않는다."""
    prior = _turn("산책 후 발을 절뚝거려", "정확한 원인은 진단할 수 없어요. 동물병원에 방문해 보세요.")
    service = _service_with(resolver=_fixed(TurnRelation.REPEAT, referenced=prior))
    response = await service.run(
        query="그러니까 발을 저는 이유가 뭘 수 있는지 알고 싶다고",
        principal=PrincipalContext(kind="app_user", permissions=[]),
        prior_turns=[prior],
    )
    assert response.message.strip() != prior.assistant.strip()


@pytest.mark.anyio
async def test_meta_does_not_fall_through_to_off_topic() -> None:
    """수용 케이스 6 — 대화 자체에 대한 말은 off_topic 거절이 아니다."""
    prior = _turn("오늘 건강 상태는 어때?", "증상의 원인이나 병명은 여기서 판단하지 않아요.")
    service = _service_with(resolver=_fixed(TurnRelation.META, referenced=prior))
    response = await service.run(
        query="그니까 그걸 네가 나한테 물어봐야지",
        principal=PrincipalContext(kind="app_user", permissions=[]),
        prior_turns=[prior],
    )
    assert response.status in (AssistantStatus.ANSWERED, AssistantStatus.CLARIFY)
    assert response.status is not AssistantStatus.REFUSED


@pytest.mark.anyio
async def test_follow_up_after_a_clarification_carries_the_asked_axes() -> None:
    """수용 케이스 5 — 되묻기 뒤 후속 답변이 원 질문에 붙고, 축은 '물은 것' 으로만 간다."""
    pending = PendingClarification(
        turn_id=uuid.uuid4(),
        question="식욕과 활력 중 어느 쪽이 달라 보이나요?",
        missing_axes=[ObservationAxis.APPETITE, ObservationAxis.ENERGY],
    )
    captured: dict = {}
    service = _service_with(
        resolver=_fixed_pending(TurnRelation.FOLLOW_UP, pending), general_sink=captured
    )
    await service.run(
        query="밥은 먹는데 계속 누워 있어",
        principal=PrincipalContext(kind="app_user", permissions=[]),
        pending_clarification=pending,
    )
    conversation = captured["payload"].conversation
    assert conversation is not None
    assert conversation.pending_question == pending.question
    assert conversation.pending_missing_axes == [ObservationAxis.APPETITE, ObservationAxis.ENERGY]


@pytest.mark.anyio
async def test_correction_hands_the_corrected_frame_down() -> None:
    """수용 케이스 3 — '아니, 산책 말고 밥' 의 정정된 프레임이 아래로 간다."""
    prior = _turn("산책 얼마나 시켜야 해?", "하루 30분 이상을 권합니다.")
    captured: dict = {}
    service = _service_with(
        resolver=_fixed(
            TurnRelation.CORRECTION, referenced=prior, standalone="밥을 얼마나 줘야 해?"
        ),
        general_sink=captured,
    )
    await service.run(
        query="아니, 산책 말고 밥",
        principal=PrincipalContext(kind="app_user", permissions=[]),
        prior_turns=[prior],
    )
    conversation = captured["payload"].conversation
    assert conversation.relation is TurnRelation.CORRECTION
    assert conversation.standalone_query == "밥을 얼마나 줘야 해?"
    assert conversation.referenced_original_request == "산책 얼마나 시켜야 해?"


@pytest.mark.anyio
async def test_follow_up_pronoun_reaches_the_answerer() -> None:
    """수용 케이스 2 — '그거 얼마나 자주 해?' 가 앞 요청에 붙는다."""
    prior = _turn("심장사상충 예방약 먹여야 해?", "네, 보통 한 달에 한 번 투여합니다.")
    captured: dict = {}
    service = _service_with(
        resolver=_fixed(
            TurnRelation.FOLLOW_UP, referenced=prior, standalone="심장사상충 예방약을 얼마나 자주 먹여?"
        ),
        general_sink=captured,
    )
    await service.run(
        query="그거 얼마나 자주 해?",
        principal=PrincipalContext(kind="app_user", permissions=[]),
        prior_turns=[prior],
    )
    assert captured["payload"].conversation.referenced_original_request == prior.user
```

`_fixed` · `_fixed_pending` · `general_sink` 헬퍼는 이 파일 안에 만든다. `general_sink` 는 `orchestrator_comparison.runner_v2.RecordingEngine` 과 같은 자리 — General 어댑터에 닿은 `payload` 를 얕게 가로챈다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && uv run pytest tests/test_orchestration_turn_resolver_wiring.py -q`
Expected: FAIL — 헬퍼 미구현

- [ ] **Step 3: 헬퍼를 만들고 통과시킨다**

이 태스크는 **테스트만 추가한다.** 실패하면 Task 5·6 의 배선이 모자란 것이므로 그쪽을 고친다 — 여기서 프로덕션 코드를 새로 쓰지 않는다.

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && uv run pytest tests/test_orchestration_turn_resolver_wiring.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/tests/test_orchestration_turn_resolver_wiring.py
git commit -m "test: 수용 케이스 2·3·4·5·6 — 관계가 아래에서 무엇을 바꾸는지 고정한다"
```

---

### Task 9: `SessionDriver` 와 상수 뒤집기

**Files:**
- Modify: `backend/src/daengs_evals/conversation_quality/drivers.py`
- Modify: `backend/src/daengs_evals/conversation_quality/collect.py`
- Modify: `backend/src/daengs_evals/conversation_quality/transcript.py`
- Modify: `backend/src/daengs_evals/conversation_quality/__main__.py`
- Test: `backend/tests/test_conversation_quality_session_driver.py`

**Interfaces:**
- Consumes: Task 7 의 `PriorTurn` · `PendingClarification`
- Produces: `drivers.SessionDriver`, `collect.build_session_driver(adapter_mode)`

**`collect.py` 의 `target_turn_row` · `judge.py` · `report.py` 는 안 고친다.** 고쳐야 한다면 이음매가 샌 것이다(#401 §5 의 약속). `collect.py` 는 `build_session_driver` 한 함수만 는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# backend/tests/test_conversation_quality_session_driver.py
from daengs_evals.conversation_quality.drivers import SessionDriver
from daengs_evals.conversation_quality.transcript import PRIOR_TURNS_REACH_INFERENCE


def test_the_constant_is_flipped() -> None:
    """이 상수 하나가 카드와 하네스를 잇는 자리다 (#401 §3)."""
    assert PRIOR_TURNS_REACH_INFERENCE is True


def test_session_driver_accumulates_turns_and_reports_indices_not_text() -> None:
    """랩 파일에 본문을 안 적는다 — 본문은 이미 cases_v1.jsonl 에 있다 (스펙 ④)."""
    driver = SessionDriver(_recording_orchestrator(), principal=None, adapter_mode="fake")
    driver.send("심장사상충 예방약 먹여야 해?")
    payload = driver.send("그거 얼마나 자주 해?")
    assert payload["prior_turns_supplied"] == [0]
    assert "심장사상충" not in repr(payload["prior_turns_supplied"])


def test_session_driver_carries_a_pending_clarification_forward() -> None:
    driver = SessionDriver(_clarifying_orchestrator(), principal=None, adapter_mode="fake")
    driver.send("오늘 건강 상태는 어때?")
    driver.send("밥은 먹는데 계속 누워 있어")
    assert driver.last_pending_question is not None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && uv run pytest tests/test_conversation_quality_session_driver.py -q`
Expected: FAIL — `ImportError: cannot import name 'SessionDriver'`

- [ ] **Step 3: 구현**

`SessionDriver` 는 `StatelessDriver` **옆에** 둔다(상속하지 않는다 — 두 드라이버가 각자 정직하게 자기가 보낸 것을 말해야 한다). `send()` 가 `prior_turns` 를 쌓고 `orchestrator.run(..., prior_turns=…, pending_clarification=…)` 로 보낸다. 반환 payload 는 `StatelessDriver` 와 **같은 칸**을 쓰고 `prior_turns_supplied` 에 **인덱스만** 적는다. `clarify` 칸은 #415 의 `_sanitize_clarify` 를 그대로 쓴다.

`__main__.py` 에 `--driver {stateless,session}` 를 더한다 (기본 `stateless` — 기존 명령이 안 바뀐다).

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && uv run pytest tests/test_conversation_quality_session_driver.py tests/test_conversation_quality_case_report.py -q`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add backend/src/daengs_evals/conversation_quality/ backend/tests/test_conversation_quality_session_driver.py
git commit -m "feat: SessionDriver 와 PRIOR_TURNS_REACH_INFERENCE 뒤집기 — 하네스는 안 고친다"
```

---

### Task 10: 문서·리포트 라벨과 전체 검증

**Files:**
- Modify: `docs/orchestration/conversation-quality.md` (§3 의 두 축 기대 정답, §7 카드 B 의 「받아들이는 법」)
- Modify: `backend/src/daengs_evals/conversation_quality/report.py` (before 열의 「기능 부재」 라벨)
- Modify: `docs/decisions.md` (D-069 초안)
- Modify: `docs/superpowers/specs/2026-09-10-assistant-turn-context-design.md` (§6 을 조건부 버전으로)

- [ ] **Step 1: 리포트 라벨 테스트를 쓴다**

```python
def test_before_lap_is_labelled_feature_absent_not_model_failure() -> None:
    """두 축의 before 0 은 '모델이 나빴다' 가 아니라 '기능이 없었다' 이다."""
    rendered = render_compare(before=_before_lap(), after=_after_lap())
    assert "기능 부재" in rendered
    assert "품질 개선" not in rendered
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && uv run pytest tests/test_conversation_quality_case_report.py -q -k "feature_absent"`
Expected: FAIL

- [ ] **Step 3: 라벨과 문서를 고친다**

`transcript.PRIOR_TURNS_REACH_INFERENCE` 가 `True` 가 됐으므로 `conversation-quality.md` §3 의 「두 축의 기대 정답은 0」 문단을 고치고, §7 카드 B 에 **"Turn Resolver 로 구현됨 — 스펙 링크"** 를 적는다. D-069 초안을 `docs/decisions.md` 에 더한다: *"대화 이어짐은 이력 전달이 아니라 관계 판정으로 한다 — Turn Resolver 는 라우팅 앞에 서고 응급 뒤에 선다."*

- [ ] **Step 4: 랩 설계를 문서로 못박는다 (돌리지는 않는다)**

`backend/evals/conversation_quality/README.md` 에 이 카드의 랩 설계를 적는다. **돌리는 것은
사람이 실제 Gemini 호출을 승인한 뒤다.**

- **before 랩은 `after_v2_346cada0/lap_after.jsonl` 이다.** `lap_before.jsonl` 이 아니다 —
  그것은 `#415` 이전이라 이 카드의 출발선이 아니다. `#415` 가 `cq_pronoun_geugeo_01` ·
  `cq_pronoun_akka_01` 의 현재 동작을 **ANSWERED(맥락을 지어냄) → CLARIFY(못 찾겠다고 말함)**
  로 바꿔 놨고, 이 카드의 목표는 거기서 **ANSWER** 로 가는 것이다. 이 둘이 가장 깨끗한
  수용 신호다.
- **`case_report.py` 를 그대로 쓴다** (`case-report --before-lap --after-lap`). 판정기를 안
  불러서 공짜다. 새 리포트를 만들지 않는다.
- ⚠ **응급 대조군은 지금 랩에서 못 읽는다.** 하네스에 `vet_contact` 어댑터가 없어 after 랩
  두 번 모두 `FAILED`("지원하지 않는 기능입니다: vet_contact") 였다. 물리면 place-search HTTP
  의존이 생긴다. **이 카드는 안 문다** — 수용 케이스 8(응급 우선)은 Task 5 의 오케스트레이션
  테스트가 이미 잡고 있고, 그쪽은 HTTP 가 필요 없다. 랩에서는 그 행을 **미측정**으로 적는다.
- ⚠ **라우터가 실행마다 다른 능력을 고른다** — temperature 0 인데도 같은 응급 질의가 before 는
  `general`, after 두 번은 `vet_contact` 였다. 멀티턴 랩은 이 흔들림이 더 커지므로,
  1회 실행의 차이를 개선·퇴행으로 읽지 않는다. 반복 횟수를 리포트에 적는다.
- **여섯을 고정한다** — 케이스 파일 해시 · judge 모델 핀 · judge 프롬프트 버전 · 앵커 세트 ·
  어댑터 모드 · 미측정 비율 정의.

- [ ] **Step 5: 전체 검증**

```bash
cd backend
uv run check                                   # 3초. 저장소 규칙
uv run pytest                                  # 전체, 약 9분
uv run ruff check src/daengs_backend/orchestration/resolver.py src/daengs_evals/conversation_quality/
```

Expected: `check` 통과 · `pytest` 전부 통과(skip 은 DB 필요 건만) · ruff 통과

`docs/ci/README.md` 의 로컬 게이트 목록을 읽고 이 변경에 해당하는 것을 돌린다. **`db/` 를 안 건드렸으므로 버리는 Postgres 가 필요한 검사 둘은 해당 없다** — 해당 없음을 확인한 사실로 PR 본문에 적는다.

- [ ] **Step 6: 커밋**

```bash
git add docs/ backend/evals/conversation_quality/README.md         backend/src/daengs_evals/conversation_quality/report.py backend/tests/
git commit -m "docs: 두 축의 기대 정답을 다시 정하고 랩의 출발선을 after_v2 로 못박는다"
```

- [ ] **Step 7: 사람에게 넘길 것 하나를 PR 본문에 적는다**

`cq_repeat_after_failure_01` 의 `expected_mode` 는 **열려 있다** (PR 본문 ⑧). after_v2 에서
그 케이스는 `REFUSED` 이고, `그러니까 발을 저는 이유가 뭘 수 있는지 알고 싶다고` 가 원인을
명시적으로 요구하므로 승인된 의료 경계 규칙대로면 `REFUSED/diagnosis` 가 맞을 수 있다 —
케이스의 `ASK` 기대 쪽이 낡았을 가능성이다. **이 카드가 정하지 않는다.** 정리 전에 랩을 돌리면
제대로 고쳐 놓고도 그 행이 빨갛게 떠서 자기 실패로 착각한다. PR 본문에 그렇게 적고 사람 결정을
기다린다.

Task 8 의 `test_repeat_does_not_replay_the_same_fixed_refusal` 은 이 미정에 안 흔들린다 —
"같은 문구를 두 번 내보내지 않는다" 만 보므로 `REFUSED` 든 `ASK` 든 통과한다.

---

## Self-Review

**1. 스펙 커버리지**

| 스펙 | 태스크 |
| --- | --- |
| §3 책임 1 (다섯 관계로 분류) | 1, 3, 4 |
| §3 책임 2 (앞 요청·대기 되묻기 지정) | 3, 7 |
| §3 책임 3 (제한된 구조화 컨텍스트) | 5, 6 |
| §3 책임 4 (불확실하면 CLARIFY) | 5 |
| §3 fast path | 1, 4 |
| §3 후보 경계 (3쌍 + 대기는 별도) | 2, 7 |
| ① 개수·종류 | 2, 7 |
| ② 누가 보는가 | 5, 6 |
| ③ 길이 한도 | 2 |
| ④ 프라이버시·로깅 | 5(LOGGER), 9(랩 파일) |
| ⑤ 이력 없을 때 | 1, 6 |
| ⑥ CLARIFY 이음 | 7 |
| ⑦ 오염 방지 넷 | 1(창·NEW), 3(배치), 5(응급 순서) |
| ⑧ 수용 케이스 9건 | 1(1·7), 5(8·9), 8(2·3·4·5·6) |
| §5 이음매 | 5, 6, 7 |
| §6 프롬프트 버전 | 6, 10 |
| §7 측정 | 9, 10 |

빠진 것 없음.

**2. 플레이스홀더 스캔**

Task 5 Step 3 의 `_service_with` · Task 8 의 `_fixed`/`general_sink` 는 "기존 테스트 파일의 조립 방식을 따른다" 로 넘긴 자리다. 그 파일 이름(`tests/test_orchestration_semantic_router.py`)을 명시했으므로 실행자가 찾아갈 수 있다. Task 4 Step 3 의 `_generate_with_gemini` 도 따라갈 원본(`semantic._generate_with_gemini`)을 지목했다.

**3. 타입 일관성**

- `PriorTurn` 은 Task 1 에서 `turn_id`·`user`·`assistant` 로 정의되고 2·4·7·9 에서 같은 이름으로 쓰인다.
- `resolve(*, query, candidates, pending)` 는 4·5·9 에서 같다.
- `ConversationContext` 는 5 에서 정의되고 6·8 에서 같은 필드명으로 쓰인다.
- `general_prompt_version(payload)` 는 6 에서만 쓰인다.
- **`ConversationContext` 의 정의 위치가 Task 1(`resolver.py`)인데 Task 5 에서 `contracts.GeneralPayload` 가 그것을 참조한다.** `contracts.py` 는 `resolver.py` 를 import 하면 안 된다(`resolver` 가 `contracts` 를 import 하므로 순환). **→ `ConversationContext` 는 `contracts.py` 에 두고 `resolver.py` 가 그것을 import 한다.** Task 1 Step 3 과 Task 5 를 그렇게 읽을 것.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-11-assistant-turn-resolver.md`.
