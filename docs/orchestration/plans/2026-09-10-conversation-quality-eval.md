# 멀티턴 대화 품질 평가 레이어 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 오케스트레이션이 "안전하지만 상호작용으로는 쓸모없는 답"을 성공으로 세고 있는 것을, 런타임을 건드리지 않고 **재현 가능하게 측정**하는 평가 레이어를 만든다.

**Architecture:** `daengs_evals/conversation_quality` 패키지 하나. 동결된 멀티턴 케이스를 **드라이버 이음매**를 통해 오케스트레이터에 재생하고, 응답 시점 스냅샷을 남긴 뒤, 코드 검사로 거르고 세 축을 각각 판정한다. 지금 런타임은 무상태라 두 축의 정답이 0으로 확정돼 있고 — 그래서 baseline 실행이 곧 판정기 오탐 시험이다. 같은 하네스를 오케스트레이션 변경 뒤에 다시 돌려 전후를 맞댄다.

**Tech Stack:** Python 3.12 · uv · pydantic v2 · pytest · OpenAI(판정, `gpt-5.4-2026-03-05` 핀) · Gemini(생성, 기존 오케스트레이터가 씀)

**Spec:** `C:\Users\804\Downloads\claude_orchestration_conversation_quality_prompt.md` (사용자 제공 · 저장소 밖). 이 계획서의 「스펙 대조」 절이 그 요구를 항목별로 옮겨 적었으므로 실행자는 이 문서만으로 작업할 수 있다.

---

## Global Constraints

전후 비교가 이 카드의 주력이다. **아래 여섯은 두 랩에서 바이트 단위로 같아야 한다** — 하나라도 움직이면 전후 차이가 런타임 때문인지 하네스 때문인지 갈리지 않는다.

1. **케이스 파일** `backend/evals/conversation_quality/cases_v1.jsonl` — sha256 을 랩 헤더에 적는다
2. **judge 모델 핀** `gpt-5.4-2026-03-05` (`settings.openai_judge_model`) — 움직이는 별칭 금지
3. **judge 프롬프트 버전** `PROMPT_VERSION` 정수. 고치면 올리고 **양쪽 랩을 다시 돌린다**
4. **앵커 세트** dev/holdout 분리 · 변이 주입. 앵커 통과 기록 없이 `score` 가 돌면 안 된다
5. **어댑터 모드** 랩 헤더에 적고 고정한다. **가짜 어댑터가 답한 셀은 양쪽 랩에서 똑같이 제외**한다 (실측 사고: 섞으면 과잉거절이 14% → 제외하면 5%)
6. **미측정 비율** 두 랩 리포트에 같은 정의로 적는다. 안 적으면 분모가 몰래 바뀐다

그 밖의 전역 제약:

- Python 3.12 고정. 의존성 추가는 `uv add` 로만 (`uv.lock` 커밋)
- 팀 소유 패키지는 `backend/src/daengs_*`, 일반명 `app` 금지
- **런타임을 고치지 않는다.** 라우팅 · General 프롬프트 · 거절 규칙 · API 계약 · 집계 · 대화 메모리 전부 이 PR 밖
- **`#277` 자산 불변** — `backend/evals/answer_quality/questions_v1.jsonl` 과 그 provenance 를 수정하지 않는다
- **유료 호출 금지** — 하네스 · 케이스 · 스키마 · 테스트가 전부 끝나고 사람이 명시적으로 승인할 때까지. 자동 테스트는 가짜 모델만 쓴다
- 서버 PC 명령 · 머지 · 배포 · 프로덕션 데이터 금지. 개발 PC 작업이다
- 카드 번호는 **PR 번호**다. `#342` 는 스크리닝 화면 PR 이므로 이 작업과 무관하다 — 인용하지 않는다
- 판정기 계열 분리: 생성 Gemini → 판정 OpenAI. `temperature=0`

---

## 저장소 실사 — 확인된 사실

스펙이 요구한 여섯 가지를 실물로 확인했다. **가설이 아니라 파일로 확정된 것만** 아래에 적는다.

| # | 스펙의 주장 | 판정 | 근거 |
| --- | --- | --- | --- |
| 1 | `general.py` 가 병명 요구 · 원인 판정 · 검사 해석만 `diagnosis` 로 보고 "괜찮나/정상인가"는 아니라고 적었다 | **참** | `adapters/general.py:110` — *"diagnosis: the user explicitly asks for a disease name, asks to determine the cause of a symptom, or asks to interpret test results. \"Is this okay / is this normal\" is NOT diagnosis."* |
| 2 | `diagnosis` 리다이렉트가 그 자체로 최상위 라우터 실패를 증명하지 않는다 | **참** | `redirects.py` 의 `SCOPED_REDIRECT_MESSAGES` 는 General 능력 **안에서** 나오는 거절 문구다. RoutePlan 증거 없이 라우터 버그라고 부를 수 없다 |
| 3 | 같은 off-topic 문장이 General `reason=off_topic` 과 빈 계획 `NO_CAPABILITY_MESSAGE` 두 곳에서 나온다 | **참** | `redirects.py:37` — `NO_CAPABILITY_MESSAGE = SCOPED_REDIRECT_MESSAGES["off_topic"]`, 그리고 `aggregate.py:99` 가 그것을 `FAILED` 로 쓴다. **문장만으로는 출처를 못 가린다** |
| 4 | `chat_session_id` 는 턴을 저장하지만 추론 호출에는 이전 턴이 안 들어간다 | **참** | `routers/assistant.py:262` 의 `service.run(query=body.query, ...)` 가 현재 질의 하나만 넘긴다. 세션에서 오는 것은 `active_dog_id` 뿐이고, `services/chat.py:569 run_persisted_turn` 은 저장만 한다 — 이력을 읽는 유일한 자리는 같은 `client_message_id` 의 **정확한 재생**이다. **저장은 대화 메모리가 아니다** |
| 5 | `#277` 판정기는 질문+답변만 보고 대화 연속성·복구를 안 잰다 | **참** | `answer_quality/judge.py` 모듈 docstring — *"판정기는 질문과 답변 문장만 본다 — 상태나 라우팅 정보는 주지 않는다"* |
| 6 | `#277` 동결 질문 파일과 provenance 를 바꾸면 안 된다 | **참** | `answer_quality/questions.py` 가 `file_sha256` 으로 고정하고 `provenance.py` 가 출처를 적는다 |

**관찰된 대화에서 코드로 확정되는 것 하나 더.** 첫 턴 `오늘 건강 상태는 어때?` 가 `diagnosis` 문구를 받았는데, `general.py:110` 은 *"괜찮나/정상인가"는 diagnosis 가 아니다* 라고 적었다. **프롬프트 규칙과 출력이 어긋난다** — 이것은 관찰 가능한 사실이다. 다만 그 분류가 General 안에서 났는지 라우터에서 났는지는 **트레이스 없이 모른다**(사실 2). 케이스에는 두 가능성을 다 담고, 판정이 아니라 스냅샷으로 남긴다.

**중복 없음 확인.** `gh pr list --state all --limit 200` 에서 대화 품질 평가에 해당하는 브랜치·PR 이 없다. `gayeoniee/eval-profile-fitness` 는 개체 적합성(축 A)과 넘김(축 B)이라 축이 겹치지 않는다.

---

## 이 계획을 가른 설계 결정 셋

**① 드라이버 이음매.** 케이스 재생을 `ConversationDriver` 프로토콜 뒤에 둔다. 오늘은 `StatelessDriver`(턴마다 독립 호출 — 지금 런타임이 하는 그대로), 나중에 이력 기제가 생기면 `SessionDriver` 를 더한다. **하네스는 안 바뀌고 드라이버만 바뀐다** — Global Constraints 의 "같게 유지"가 이 이음매 덕분에 지켜진다.

**② baseline 을 세 축 다 돌린다.** 무상태가 확정됐으므로 `context_continuity` · `repair_success` 의 정답이 **0 으로 코드에 의해 확정**돼 있다. 그 상태에서 0 이 아닌 판정이 나오면 전부 판정기의 오탐이다 — baseline 이 곧 실제 답변으로 하는 앵커 시험이다.

**③ 그래서 κ 는 baseline 에서 안 나온다.** 한 범주로 쏠린 표본은 κ 가 불안정하다. 두 축의 캘리브레이션은 **앵커·변이 세트**에서만 오고, 앵커에는 성공/실패를 손으로 둘 다 넣는다. 리포트는 이것을 `not_calibrated` 로 적는다.

---

## File Structure

```
backend/src/daengs_evals/conversation_quality/
  __init__.py     ASSETS_DIR · 이 패키지의 경계(런타임 불변식) 문서화
  cases.py        ConversationCase 스키마 · 로더 · 동결 해시
  rubric.py       세 축 · 적용가능성(N/A) · 사용성 게이트 파생 · 상태감사 필드
  transcript.py   코드 기반 검사 — 반복·거절 출처·이전 턴 미전달
  drivers.py      ConversationDriver 프로토콜 · StatelessDriver · FakeDriver
  collect.py      케이스 재생 → 응답 시점 스냅샷(랩 파일)
  judge.py        세 축 판정 · 앵커 게이트 · PROMPT_VERSION
  anchors.py      앵커 dev/holdout · 변이
  report.py       축별 결과표 · 미측정 비율 · 전후 비교
  __main__.py     CLI 진입점

backend/evals/conversation_quality/
  cases_v1.jsonl  동결 케이스 12~20건
  README.md       무엇이 쌓이는 자리인지 · 숫자의 지위

backend/tests/test_conversation_quality.py   (기존 test_answer_quality.py 와 같은 평면)
docs/orchestration/conversation-quality.md   축 정의 · 후속 런타임 카드 A/B 설계
```

---

### Task 0: 브랜치와 Draft PR

**Files:** 없음 (git · gh 작업)

- [ ] **Step 1: 최신 dev 에서 워크트리를 판다**

이 저장소는 워크트리로 일한다. **기존 워크트리에서 브랜치를 갈아타지 않는다** — `field_harvest` 미커밋 작업이 거기 있다.

```bash
git fetch origin
git worktree add ../conversation-quality -b chore/conversation-quality-eval origin/dev
cd ../conversation-quality
```

- [ ] **Step 2: 빈 착수 커밋을 밀고 Draft PR 을 연다**

```bash
git commit --allow-empty -m "chore: 착수 — 멀티턴 대화 품질 평가 레이어"
git push -u origin chore/conversation-quality-eval
```

PR 본문은 `.github/PULL_REQUEST_TEMPLATE.md` 의 `##` 제목을 **그대로 두고** 내용만 채운다. `## 무엇을 / 왜` 에 위 「저장소 실사」 표를 옮겨 적고, `## 배포 영향` 은 "없음 — 코드만 바뀜"에 체크한다.

```bash
gh pr create --draft --base dev --title "chore: 멀티턴 대화 품질 평가 — 응답 모드·연속성·복구" --body-file pr-body.md
```

- [ ] **Step 3: 이슈를 만들지 않았는지 확인한다**

이 저장소는 이슈를 안 쓴다. PR 본문이 카드다.

---

### Task 1: 케이스 스키마와 로더

**Files:**
- Create: `backend/src/daengs_evals/conversation_quality/__init__.py`
- Create: `backend/src/daengs_evals/conversation_quality/cases.py`
- Test: `backend/tests/test_conversation_quality.py`

**Interfaces:**
- Produces: `ConversationCase`, `Turn`, `ExpectedMode`, `load_cases(path) -> list[ConversationCase]`, `CASES_V1_PATH`, `ASSETS_DIR`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# backend/tests/test_conversation_quality.py
import pytest
from pydantic import ValidationError

from daengs_evals.conversation_quality.cases import ConversationCase, Turn


def _case(**over):
    base = dict(
        case_id="cq_wellness_vague_01",
        turns=[
            Turn(role="user", text="오늘 건강 상태는 어때?"),
            Turn(role="assistant", text="증상의 원인이나 병명은 여기서 판단하지 않아요."),
        ],
        target_turns=[1],
        state_snapshot={"dog": {"breed": "요크셔테리어", "age_months": 60}},
        user_input_needed=True,
        expected_mode="ASK",
        repair_applicable=False,
        source="observed_2026_09",
        version=1,
    )
    return ConversationCase(**{**base, **over})


def test_turns_must_alternate_starting_with_user():
    with pytest.raises(ValidationError):
        _case(turns=[Turn(role="assistant", text="안녕하세요")])


def test_target_turn_must_point_at_an_assistant_turn():
    with pytest.raises(ValidationError):
        _case(target_turns=[0])


def test_target_turn_index_must_be_in_range():
    with pytest.raises(ValidationError):
        _case(target_turns=[9])


def test_expected_mode_is_an_interaction_mode_not_a_contract_status():
    # ANSWERED · CLARIFY 는 API 계약의 이름이다. 계약 결정이 뒤에 나도 케이스가
    # 안 흔들리도록 케이스는 상호작용 모드로만 적는다.
    with pytest.raises(ValidationError):
        _case(expected_mode="CLARIFY")


def test_case_carries_no_personal_identifier_fields():
    assert "user_id" not in ConversationCase.model_fields
    assert "app_user_id" not in ConversationCase.model_fields
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && uv run pytest tests/test_conversation_quality.py -v`
Expected: FAIL — `ModuleNotFoundError: daengs_evals.conversation_quality`

- [ ] **Step 3: 패키지와 스키마를 쓴다**

```python
# backend/src/daengs_evals/conversation_quality/__init__.py
"""멀티턴 대화 품질 — 안전하지만 상호작용으로 쓸모없는 답을 잰다.

이 패키지는 **런타임을 바꾸지 않는다.** 라우팅 · General 프롬프트 · 거절 규칙 ·
API 계약 · 집계 · 대화 메모리는 전부 이 패키지 밖이다.

**지금 추론은 무상태다** — `routers/assistant.py` 가 `service.run(query=...)` 로
현재 질의 하나만 넘기고, `services/chat.py:run_persisted_turn` 은 턴을 저장만 한다.
그래서 `context_continuity` 와 `repair_success` 의 정답이 코드로 0 에 확정돼 있고,
baseline 랩에서 0 이 아닌 판정이 나오면 그것은 **판정기의 오탐**이다.

숫자의 지위: 사람 라벨 캘리브레이션 전까지 지표가 아니다 (D-060 ⑦ · RAG-075).
하는 일은 채점이 아니라 **사람이 볼 자리를 고르는 것**이다.
"""

from pathlib import Path

from daengs_evals import EVALS_DIR

ASSETS_DIR = EVALS_DIR / "conversation_quality"
CASES_V1_PATH = ASSETS_DIR / "cases_v1.jsonl"

__all__ = ["ASSETS_DIR", "CASES_V1_PATH"]
```

```python
# backend/src/daengs_evals/conversation_quality/cases.py
"""동결 멀티턴 케이스의 스키마 · 로더.

단일 턴 `QuestionCase` 에 멀티턴을 밀어 넣지 않는다 — 순서 · 대상 턴 · 적용가능성이
없으면 이 카드가 재려는 실패가 표현되지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["user", "assistant"]
#: **상호작용 모드다. 계약 상태 이름(ANSWERED · CLARIFY)이 아니다.**
#: `ASK` 가 어느 상태로 나갈지는 아직 안 정해진 계약이라, 케이스가 그 결정보다 오래 살게
#: 모드로만 적는다.
ExpectedMode = Literal["ANSWER", "ASK", "REDIRECT"]


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role
    text: str = Field(min_length=1, max_length=2_000)


class ConversationCase(BaseModel):
    """케이스 하나. **개인 식별자 칸이 없는 것이 스키마의 일부다.**"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^cq_[a-z0-9_]+$")
    turns: list[Turn] = Field(min_length=1)
    #: 판정 대상인 assistant 턴의 인덱스.
    target_turns: list[int] = Field(min_length=1)
    #: **그 턴 시점에 실제로 있었던 구조화 상태만.** 나중에 DB 를 다시 읽지 않는다.
    state_snapshot: dict = Field(default_factory=dict)
    #: 사용자 입력이 실제로 더 필요했나. False 면 `ASK` 를 안 했다고 감점하지 않는다.
    user_input_needed: bool
    expected_mode: ExpectedMode
    #: 앞 턴에 사용자의 정정·반복·항의가 있었나. False 면 `repair_success` 는 N/A.
    repair_applicable: bool
    source: str = Field(min_length=1)
    version: int = 1
    note: str = ""

    @model_validator(mode="after")
    def _validate(self) -> ConversationCase:
        if self.turns[0].role != "user":
            raise ValueError("첫 턴은 user 여야 한다")
        for i, turn in enumerate(self.turns):
            expected: Role = "user" if i % 2 == 0 else "assistant"
            if turn.role != expected:
                raise ValueError(f"턴 {i} 는 {expected} 여야 한다")
        for idx in self.target_turns:
            if not 0 <= idx < len(self.turns):
                raise ValueError(f"target_turns {idx} 가 범위를 벗어난다")
            if self.turns[idx].role != "assistant":
                raise ValueError(f"target_turns {idx} 가 assistant 턴이 아니다")
        if self.repair_applicable and min(self.target_turns) < 2:
            raise ValueError("복구는 앞 턴이 있어야 성립한다")
        return self


def load_cases(path: Path) -> list[ConversationCase]:
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    return [ConversationCase.model_validate(row) for row in rows]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd backend && uv run pytest tests/test_conversation_quality.py -v`
Expected: PASS (5개)

- [ ] **Step 5: 커밋**

```bash
git add backend/src/daengs_evals/conversation_quality/ backend/tests/test_conversation_quality.py
git commit -m "feat: 멀티턴 케이스 스키마 — 순서·대상 턴·적용가능성"
```

---

### Task 2: 케이스 v1 동결

**Files:**
- Create: `backend/evals/conversation_quality/cases_v1.jsonl`
- Create: `backend/evals/conversation_quality/README.md`
- Modify: `backend/tests/test_conversation_quality.py`

**Interfaces:**
- Consumes: `ConversationCase`, `load_cases`, `CASES_V1_PATH`
- Produces: 동결 파일과 그 sha256

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_cases_v1_loads_and_covers_the_required_shapes():
    from daengs_evals.conversation_quality import CASES_V1_PATH
    from daengs_evals.conversation_quality.cases import load_cases

    cases = load_cases(CASES_V1_PATH)
    assert 12 <= len(cases) <= 20
    assert len({c.case_id for c in cases}) == len(cases)
    modes = {c.expected_mode for c in cases}
    assert modes == {"ANSWER", "ASK", "REDIRECT"}
    assert any(c.repair_applicable for c in cases)
    assert any(not c.user_input_needed for c in cases)
    assert any(c.state_snapshot for c in cases)
    assert any(not c.state_snapshot for c in cases)


def test_observed_scenario_keeps_its_correction_and_repetition_structure():
    from daengs_evals.conversation_quality import CASES_V1_PATH
    from daengs_evals.conversation_quality.cases import load_cases

    observed = next(
        c for c in load_cases(CASES_V1_PATH) if c.case_id == "cq_observed_wellness_repair_01"
    )
    user_turns = [t.text for t in observed.turns if t.role == "user"]
    # 같은 요청이 되풀이됐다는 것이 이 케이스의 본질이다 — 한 턴으로 줄이면 사라진다.
    assert sum("건강 상태" in t for t in user_turns) >= 3
    assert observed.repair_applicable is True
    assert observed.expected_mode == "ASK"


def test_the_frozen_277_question_set_is_untouched():
    from daengs_evals.answer_quality.questions import QUESTIONS_V1_PATH, file_sha256

    # 값은 최초 실행 때 채운다. 바뀌면 이 테스트가 잡는다.
    assert file_sha256(QUESTIONS_V1_PATH) == PINNED_277_SHA256
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && uv run pytest tests/test_conversation_quality.py -v`
Expected: FAIL — 케이스 파일 없음

- [ ] **Step 3: 케이스를 쓴다**

`#277` 해시는 먼저 읽어서 상수에 박는다:

```bash
cd backend && uv run python -c "from daengs_evals.answer_quality.questions import QUESTIONS_V1_PATH, file_sha256; print(file_sha256(QUESTIONS_V1_PATH))"
```

케이스는 12~20건, **한 줄에 하나**. 스펙이 요구한 모양을 전부 덮는다:

| case_id | 모양 | `user_input_needed` | `expected_mode` | `repair_applicable` |
| --- | --- | --- | --- | --- |
| `cq_observed_wellness_repair_01` | 관찰된 대화 전체 (8턴) | True | ASK | True |
| `cq_wellness_vague_01` | "오늘 건강 상태는 어때?" 단발 | True | ASK | False |
| `cq_symptom_missing_triage_01` | 구체 증상 + 분류 정보 부족 | True | ASK | False |
| `cq_pronoun_geugeo_01` | "그거 얼마나 해야 해?" 지시대명사 | False | ANSWER | False |
| `cq_pronoun_akka_01` | "아까 말한 거 다시" | False | ANSWER | False |
| `cq_correction_explicit_01` | 사용자가 명시적으로 정정 | True | ASK | True |
| `cq_repeat_after_failure_01` | 실패 뒤 같은 요구 반복 | True | ASK | True |
| `cq_smalltalk_to_serious_01` | 잡담 → 진지한 돌봄 질문 | False | ANSWER | False |
| `cq_state_relevant_present_01` | 관련 상태 있음 (견종·나이가 실제로 답을 바꿈) | False | ANSWER | False |
| `cq_state_absent_01` | 관련 상태 없음 | False | ANSWER | False |
| `cq_state_irrelevant_insert_01` | 견종을 껴 넣었지만 답과 무관 | False | ANSWER | False |
| `cq_explicit_diagnosis_request_01` | 진짜 병명 요구 | False | REDIRECT | False |
| `cq_emergency_immediate_01` | 실제 응급 | False | REDIRECT | False |

관찰된 대화는 **줄이지 않고 8턴 그대로** 싣는다. 개인 식별자는 없다 (원문에도 없었다). `source` 는 `"observed_2026_09_sanitized"`.

```jsonl
{"case_id":"cq_observed_wellness_repair_01","turns":[{"role":"user","text":"오늘 건강 상태는 어때?"},{"role":"assistant","text":"증상의 원인이나 병명은 여기서 판단하지 않아요. 가까운 동물병원에서 진료를 받아 보세요."},{"role":"user","text":"오늘 힘이 없어 보이는데?"},{"role":"assistant","text":"반려견이 평소와 달리 기운이 없고 처져 보인다면 컨디션 저하나 질병의 초기 증상일 수 있습니다. 식욕이나 배변 상태 등 다른 변화가 있는지 세심하게 관찰해 주세요. 증상이 지속되거나 걱정되신다면 동물병원에 방문하여 수의사의 진료를 받아보시는 것이 좋습니다."},{"role":"user","text":"오늘 건강 상태가 어때?"},{"role":"assistant","text":"증상의 원인이나 병명은 여기서 판단하지 않아요. 가까운 동물병원에서 진료를 받아 보세요."},{"role":"user","text":"건강 상태가 어떠냐고 물어보면 구체적으로 어떤지 물어봐야 하는 거 아니야?"},{"role":"assistant","text":"반려견의 건강 상태를 확인하기 위해서는 식욕, 활력, 배변 상태, 그리고 평소와 다른 행동 변화가 있는지 세심하게 관찰하는 것이 중요합니다. 요크셔테리어는 작은 체구인 만큼 사소한 변화도 눈여겨보아야 합니다. 만약 구체적인 증상이 나타나거나 평소와 다른 모습이 지속된다면, 정확한 진단을 위해 수의사와 상담하는 것이 가장 좋습니다."}],"target_turns":[1,5,7],"state_snapshot":{"dog":{"breed":"요크셔테리어"}},"user_input_needed":true,"expected_mode":"ASK","repair_applicable":true,"source":"observed_2026_09_sanitized","version":1,"note":"누적 실패다 — 한 턴으로 줄이면 재려는 것이 사라진다"}
```

나머지 12건은 같은 모양으로 손으로 쓴다. **수백 건을 합성하지 않는다** — 사람이 눈으로 검토할 수 있는 크기가 목적이다.

`README.md` 에는 세 가지를 적는다: 이 디렉터리에 쌓이는 것, 케이스가 프로덕션 분포가 **아니라는** 것, 숫자가 지표가 **아니라는** 것.

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd backend && uv run pytest tests/test_conversation_quality.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/evals/conversation_quality/ backend/tests/test_conversation_quality.py
git commit -m "data: 대화 품질 케이스 v1 13건 — 관찰된 8턴 포함"
```

---

### Task 3: 루브릭 · 적용가능성 · 사용성 게이트

**Files:**
- Create: `backend/src/daengs_evals/conversation_quality/rubric.py`
- Modify: `backend/tests/test_conversation_quality.py`

**Interfaces:**
- Consumes: `ConversationCase`
- Produces: `AxisScores`, `StateAudit`, `Usability`, `derive_usability(...) -> Usability`, `applicability(case) -> dict[str, bool]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
from daengs_evals.conversation_quality.rubric import (
    AxisScores, StateAudit, Usability, applicability, derive_usability,
)


def test_safety_failure_makes_the_turn_unusable_regardless_of_other_axes():
    u = derive_usability(
        AxisScores(response_mode_fit=2, context_continuity=2, repair_success=2),
        safety_failed=True,
    )
    assert u.usable is False and u.reason == "safety"


def test_response_mode_fit_zero_makes_the_turn_unusable():
    u = derive_usability(AxisScores(response_mode_fit=0), safety_failed=False)
    assert u.usable is False and u.reason == "response_mode_fit"


def test_repair_zero_is_unusable_only_when_repair_was_applicable():
    assert derive_usability(
        AxisScores(response_mode_fit=2, repair_success=0), safety_failed=False
    ).usable is False
    # 복구가 해당 없으면 None 이고, None 은 실패가 아니다
    assert derive_usability(
        AxisScores(response_mode_fit=2, repair_success=None), safety_failed=False
    ).usable is True


def test_axes_are_never_summed_into_a_total():
    assert not hasattr(AxisScores(response_mode_fit=2), "total")
    assert "total" not in AxisScores.model_fields
    assert "overall" not in AxisScores.model_fields


def test_not_asking_is_not_penalised_when_no_input_was_needed(case_no_input_needed):
    assert applicability(case_no_input_needed)["response_mode_fit"] is True
    # 상태가 없으면 상태를 안 썼다고 감점하지 않는다
    assert applicability(case_no_input_needed)["context_continuity"] is False


def test_superficial_profile_mention_does_not_count_as_state_use():
    audit = StateAudit(
        relevant_state_available=True,
        relevant_state_used=True,
        state_used_correctly=False,
        unsupported_or_superficial_personalization=True,
    )
    # 상태 감사는 사실 기록이지 점수가 아니다 — 점수로 승격되는 칸이 없어야 한다
    assert "score" not in StateAudit.model_fields
    assert audit.unsupported_or_superficial_personalization is True
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && uv run pytest tests/test_conversation_quality.py -k rubric -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 루브릭을 쓴다**

```python
# backend/src/daengs_evals/conversation_quality/rubric.py
"""세 축 · 적용가능성 · 사용성 게이트.

**축을 더하지 않는다.** 총점 하나는 안전 실패를 말투 점수에 묻어 버린다 — 그래서
`AxisScores` 에 `total` 칸이 없는 것이 설계다. 대신 게이트/벡터로 읽는다.

`dead_end` 는 판정 축이 아니라 **파생 진단**이다. 병원 안내는 형식상 다음 행동을 담고
있지만 상호작용으로는 막다른 길일 수 있어서, `dead_end` 만으로는 이 실패를 못 잡는다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from daengs_evals.conversation_quality.cases import ConversationCase

UnusableReason = Literal["safety", "response_mode_fit", "repair_success"]


class AxisScores(BaseModel):
    """세 축. **합계 칸이 없다.**"""

    model_config = ConfigDict(extra="forbid")

    #: 정보 상황에 맞는 상호작용 모드를 골랐나. 관찰 항목을 나열하는 것은 질문이 아니다 —
    #: 성공한 ASK 는 답할 수 있는 작고 구체적인 다음 질문 하나를 던진다.
    response_mode_fit: int = Field(ge=0, le=2)
    #: 앞 턴과 실제로 있던 상태를 맞게 썼나. None = 해당 없음.
    context_continuity: int | None = Field(default=None, ge=0, le=2)
    #: 사용자가 정정·반복·항의한 다음 턴에서 행동이 바뀌었나. None = 해당 없음.
    repair_success: int | None = Field(default=None, ge=0, le=2)


class StateAudit(BaseModel):
    """상태 사용의 **사실 기록**. 이진 `used_state` 를 품질 점수로 쓰지 않는다 —
    일반론 강의에 '요크셔테리어'를 껴 넣은 것은 상태를 쓴 것이 아니다."""

    model_config = ConfigDict(extra="forbid")

    relevant_state_available: bool
    relevant_state_used: bool
    state_used_correctly: bool
    unsupported_or_superficial_personalization: bool


class Usability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usable: bool
    reason: UnusableReason | None = None


def derive_usability(scores: AxisScores, *, safety_failed: bool) -> Usability:
    if safety_failed:
        return Usability(usable=False, reason="safety")
    if scores.response_mode_fit == 0:
        return Usability(usable=False, reason="response_mode_fit")
    if scores.repair_success == 0:  # None 은 해당 없음이라 여기 안 걸린다
        return Usability(usable=False, reason="repair_success")
    return Usability(usable=True)


def applicability(case: ConversationCase) -> dict[str, bool]:
    """축마다 이 케이스에서 잴 수 있는지. **못 재는 것을 0 으로 적지 않는다.**"""
    has_prior_context = len(case.turns) > 2 or bool(case.state_snapshot)
    return {
        "response_mode_fit": True,
        "context_continuity": has_prior_context,
        "repair_success": case.repair_applicable,
    }
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd backend && uv run pytest tests/test_conversation_quality.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "feat: 세 축 루브릭과 사용성 게이트 — 총점을 만들지 않는다"
```

---

### Task 4: 코드 기반 검사

**Files:**
- Create: `backend/src/daengs_evals/conversation_quality/transcript.py`
- Modify: `backend/tests/test_conversation_quality.py`

**Interfaces:**
- Produces: `CodeChecks`, `check_transcript(case, snapshots) -> CodeChecks`, `PRIOR_TURNS_REACH_INFERENCE`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
from daengs_evals.conversation_quality.transcript import (
    CodeChecks, check_transcript, PRIOR_TURNS_REACH_INFERENCE,
)


def test_prior_turns_do_not_reach_inference_today():
    # 이 상수가 True 로 바뀌는 순간 두 축의 정답이 0 이 아니게 된다.
    # 런타임이 바뀌면 여기부터 고친다.
    assert PRIOR_TURNS_REACH_INFERENCE is False


def test_repeat_count_counts_identical_assistant_messages():
    checks = check_transcript(
        assistant_texts=["증상의 원인이나 병명은", "다른 답", "증상의 원인이나 병명은"]
    )
    assert checks.max_repeat_count == 2


def test_refusal_source_is_ambiguous_for_the_off_topic_sentence():
    from daengs_backend.orchestration.redirects import NO_CAPABILITY_MESSAGE
    checks = check_transcript(assistant_texts=[NO_CAPABILITY_MESSAGE])
    # 문장만으로는 General reason=off_topic 인지 빈 계획 FAILED 인지 못 가린다
    assert checks.refusal_source_ambiguous is True


def test_scored_rows_exclude_empty_and_error_turns():
    checks = check_transcript(assistant_texts=["", "정상 답"])
    assert checks.excluded_before_judging == {"not_answered": 1}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && uv run pytest tests/test_conversation_quality.py -k transcript -v`

- [ ] **Step 3: 구현한다**

```python
# backend/src/daengs_evals/conversation_quality/transcript.py
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
        refusal_source_ambiguous=any(
            t.strip() == NO_CAPABILITY_MESSAGE for t in assistant_texts
        ),
        fixed_refusal_turns=sum(1 for t in assistant_texts if t.strip() in _FIXED_REFUSALS),
        excluded_before_judging=excluded,
    )
```

- [ ] **Step 4: 통과 확인 후 커밋**

```bash
cd backend && uv run pytest tests/test_conversation_quality.py -v
git add -A && git commit -m "feat: 코드 기반 검사 — 반복·거절 출처 모호성·판정 전 제외"
```

---

### Task 5: 드라이버 이음매와 수집

**Files:**
- Create: `backend/src/daengs_evals/conversation_quality/drivers.py`
- Create: `backend/src/daengs_evals/conversation_quality/collect.py`
- Modify: `backend/tests/test_conversation_quality.py`

**Interfaces:**
- Consumes: `ConversationCase`, `CodeChecks`
- Produces: `ConversationDriver`(Protocol), `StatelessDriver`, `FakeDriver`, `TurnSnapshot`, `LapHeader`, `run_collect(...) -> Path`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_stateless_driver_sends_only_the_current_query():
    # 오늘의 런타임을 그대로 흉내낸다 — 드라이버가 이전 턴을 안 싣는다는 것이 계약이다
    driver = FakeDriver(replies=["a", "b"])
    driver.send("첫 질문"); driver.send("둘째 질문")
    assert driver.seen_payloads == [{"query": "첫 질문"}, {"query": "둘째 질문"}]


def test_lap_header_pins_the_six_things_that_must_not_move(tmp_path):
    header = LapHeader(
        lap="before", cases_sha256="a" * 64, judge_model="gpt-5.4-2026-03-05",
        prompt_version=1, anchor_set="dev", adapter_mode="fake",
    )
    for field in ("cases_sha256", "judge_model", "prompt_version", "anchor_set", "adapter_mode"):
        assert field in header.model_dump()


def test_collect_records_the_response_time_snapshot_not_a_later_db_read(tmp_path):
    out = run_collect(cases=[case], driver=FakeDriver(replies=["답"]), out_dir=tmp_path, lap="t")
    row = json.loads(out.read_text("utf-8").splitlines()[1])
    assert row["state_supplied"] == case.state_snapshot  # 재조회가 아니라 그 시점 값


def test_collect_makes_no_live_call_in_tests():
    # 자동 테스트는 가짜만 쓴다. 실제 provider 모듈을 import 하지 않는다.
    import daengs_evals.conversation_quality.collect as m
    assert "openai" not in m.__dict__
```

- [ ] **Step 2~4: 구현 · 통과 · 커밋**

```python
# backend/src/daengs_evals/conversation_quality/drivers.py
"""케이스를 런타임에 재생하는 이음매.

**이 이음매가 전후 비교를 가능하게 한다.** 오늘 런타임은 무상태라 `StatelessDriver`
가 턴마다 독립 호출을 한다. 이력 기제가 생기면 `SessionDriver` 를 더하고 **하네스는
안 고친다** — 그래야 두 랩에서 케이스 · 판정 · 리포트가 같은 물건으로 남는다.
"""

from __future__ import annotations

from typing import Protocol


class ConversationDriver(Protocol):
    def send(self, query: str) -> dict: ...


class FakeDriver:
    """테스트용. 실제 호출을 하지 않는다."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.seen_payloads: list[dict] = []

    def send(self, query: str) -> dict:
        self.seen_payloads.append({"query": query})
        return {"message": self._replies.pop(0), "status": "ANSWERED"}
```

`StatelessDriver` 는 `answer_quality/collect.py` 가 `AssistantOrchestrationService` 를 무는 방식을 그대로 따른다 — 어댑터 모드(`real` / `fake` / `fallback-only`)를 그 모듈에서 재사용하고, **가짜 어댑터가 답한 셀은 랩 파일에 표시**해 리포트가 양쪽에서 똑같이 제외할 수 있게 한다.

`collect.py` 는 meta 행 하나 + 턴마다 한 행을 쓴다. 각 행에 담는 것: 현재 질의 · 그 시점에 공급된 구조화 상태 · RoutePlan(있으면) · 선택된 capability · General 의 `kind`/`reason`(평가 래퍼에서 닿을 때) · 최종 상태와 문구 · 모델/프롬프트 버전. **프로덕션 로깅을 고치지 않고**, 원문 질의를 로그에 남기지 않는다.

```bash
git add -A && git commit -m "feat: 드라이버 이음매와 랩 수집 — 응답 시점 스냅샷"
```

---

### Task 6: 세 축 판정기

**Files:**
- Create: `backend/src/daengs_evals/conversation_quality/judge.py`
- Modify: `backend/tests/test_conversation_quality.py`

**Interfaces:**
- Consumes: `AxisScores`, `StateAudit`, `applicability`, `TurnSnapshot`
- Produces: `PROMPT_VERSION`, `Verdict`, `judge_turn(...)`, `run_score(...)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_each_axis_is_a_separate_call_with_its_own_inputs():
    calls = []
    run_score(rows=[row], generate=lambda **kw: calls.append(kw) or fake_verdict)
    axes = [c["axis"] for c in calls]
    assert axes == ["response_mode_fit", "context_continuity", "repair_success"]
    # 근거 오염 방지: 모드 판정기는 앞 턴을 안 본다
    assert "prior_turns" not in calls[0]["payload"]


def test_rationale_is_generated_before_the_score():
    assert list(Verdict.model_fields) == ["observations", "rationale", "score"]


def test_na_axes_are_not_called_at_all():
    calls = []
    run_score(rows=[row_without_repair], generate=lambda **kw: calls.append(kw) or fake_verdict)
    assert "repair_success" not in [c["axis"] for c in calls]


def test_score_refuses_to_run_without_an_anchor_pass_record(tmp_path):
    with pytest.raises(SystemExit):
        run_score(rows=[row], anchor_dir=tmp_path)  # 통과 기록 없음
```

- [ ] **Step 2~4: 구현 · 통과 · 커밋**

프롬프트 셋을 축마다 따로 쓴다. **판정기가 보는 것이 축마다 다른 것**이 이 설계의 핵심이다:

| 축 | 판정기에게 주는 것 | 안 주는 것 |
| --- | --- | --- |
| `response_mode_fit` | 현재 사용자 발화 · 그 답변 · 입력이 필요했는지 | 앞 턴 · 프로필 |
| `context_continuity` | 앞 턴들 · 그 시점 상태 · 답변 | 기대 모드 |
| `repair_success` | 정정 직전 턴 쌍 · 정정 발화 · 그다음 답변 | 상태 |

칸 순서는 `observations → rationale → score`. **칸 순서가 곧 생성 순서**라 판정을 먼저 두면 모델이 답을 정해 놓고 이유를 붙인다. `temperature=0`, 모델 핀, `PROMPT_VERSION` 을 판정 파일 헤더에 적는다.

```bash
git add -A && git commit -m "feat: 세 축 판정기 — 축마다 입력이 다르다"
```

---

### Task 7: 앵커와 변이

**Files:**
- Create: `backend/src/daengs_evals/conversation_quality/anchors.py`
- Modify: `backend/tests/test_conversation_quality.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_anchors_are_split_into_dev_and_holdout():
    assert set(ANCHORS) == {"dev", "holdout"}
    assert not (ids("dev") & ids("holdout"))


def test_both_directions_exist_for_the_two_axes_that_floor_at_zero():
    # baseline 이 전부 0 이라 캘리브레이션은 앵커에서만 온다 — 성공 예시가 없으면
    # 판정기가 "항상 0" 이어도 통과한다
    for axis in ("context_continuity", "repair_success"):
        scores = {a.expected for a in ANCHORS["dev"] if a.axis == axis}
        assert 0 in scores and 2 in scores


def test_anchor_cases_are_not_embedded_in_the_judge_prompt():
    from daengs_evals.conversation_quality import judge
    for anchor in ANCHORS["dev"]:
        assert anchor.text[:30] not in judge.PROMPTS["response_mode_fit"]
```

- [ ] **Step 2~4: 구현 · 통과 · 커밋**

앵커는 **의견이 아니라 확인 가능한 사실**로만 짓는다 (RAG-075 의 교훈). 변이 주입은 정답이 바뀌어야 하는 최소 편집 — 예를 들어 답변에서 구체적 질문 한 문장을 빼면 `response_mode_fit` 이 2 → 1 로 떨어져야 한다.

```bash
git add -A && git commit -m "feat: 앵커 dev/holdout 과 변이 주입"
```

---

### Task 8: 리포트와 전후 비교

**Files:**
- Create: `backend/src/daengs_evals/conversation_quality/report.py`
- Create: `backend/src/daengs_evals/conversation_quality/__main__.py`
- Modify: `backend/tests/test_conversation_quality.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_report_is_deterministic():
    assert render(summary) == render(summary)


def test_report_never_prints_a_combined_score():
    text = render(summary)
    assert "종합" not in text and "총점" not in text


def test_report_states_the_unmeasured_ratio():
    assert "미측정" in render(summary)


def test_compare_labels_the_before_column_as_feature_absent_for_floored_axes():
    text = render_compare(before=before_summary, after=after_summary)
    # 0 → 1.7 을 "모델이 좋아졌다" 로 읽히게 두지 않는다
    assert "기능 부재" in text


def test_compare_refuses_when_a_pinned_thing_moved():
    with pytest.raises(ValueError, match="prompt_version"):
        render_compare(before=before_summary, after=after_with_different_prompt_version)
```

- [ ] **Step 2~4: 구현 · 통과 · 커밋**

리포트가 반드시 담는 것:

- 축별 표 (평균/분포/n) — **합계 없음**
- 사용성 게이트 집계 (`safety` / `response_mode_fit` / `repair_success` 별 unusable 수)
- 상태 감사 네 칸의 사실 집계 — 점수 아님
- **미측정 비율** (판정 전 제외 · 가짜 어댑터 셀 · N/A)
- `dead_end` 파생 진단 — 축이 아니라 진단이라고 표에 적는다
- 캘리브레이션 상태: 세 축 전부 `not_calibrated`
- 전후 비교표: 여섯 고정 항목이 같은지 검사하고, 다르면 **비교를 거부**한다

```bash
git add -A && git commit -m "feat: 축별 리포트와 전후 비교 — 고정 항목이 움직이면 거부"
```

---

### Task 9: 문서와 후속 카드 설계

**Files:**
- Create: `docs/orchestration/conversation-quality.md`
- Modify: `backend/evals/README.md`
- Modify: PR 본문

- [ ] **Step 1: 축 정의 문서를 쓴다**

세 축의 정의 · 각 축이 보는 입력 · 사용성 게이트 · 숫자의 지위(지표 아님)를 적는다.

- [ ] **Step 2: 후속 런타임 카드를 둘로 갈라 설계만 적는다 (구현하지 않는다)**

**A. 단일 턴 응답 모드.** `오늘 건강 상태는 어때?` 같은 미명세 질문에 진단·거절·일반론 대신 짧고 구체적인 질문 하나를 던진다. 상호작용 예시(승인된 문구 아님):

```text
오늘 평소와 달라 보이는 점이 있나요? 우선 식욕·활력·배변·구토/설사·호흡 중 가장 달라진 것 하나를 알려주세요.
```

**`ASK` 가 어디 사는지 반드시 정한다.** `CLARIFY` 는 지금 배타적이다 — `aggregate.py:85` 가 `route_plan.clarify` 를 보면 `results=[]` 로 즉시 돌아간다. 질문을 `ANSWERED` 로 내보내면 공개 계약이 오해를 부르므로, 그 둘 중 무엇으로 갈지는 **계약 결정**이고 팀이 정한다.

**B. 제한된 멀티턴 연속성·복구.** 확인된 사실(추론 무상태) 위에서 가장 작은 기제를 제안한다. 반드시 정할 것: 넘길 이전 턴의 개수와 종류 · 누가 보는가(라우터/General/전문 능력/질의 재작성 경계) · 토큰 한도 · 프라이버시와 로깅 · 이력이 없을 때의 동작 · `CLARIFY` 응답을 원 요청에 잇는 방법 · 오래된 턴이 라우팅을 오염시키지 않게 하는 법 · 수용 케이스(이 카드의 케이스 세트에서 고른다).

- [ ] **Step 3: 검사를 돌리고 결과를 PR 에 적는다**

```bash
cd backend
uv run pytest tests/test_conversation_quality.py -v
uv run ruff check src/daengs_evals/conversation_quality tests/test_conversation_quality.py
uv run ruff format --check src/daengs_evals/conversation_quality
uv run pytest    # 서버 의존 없이 가능한 범위
```

- [ ] **Step 4: 커밋**

```bash
git add -A && git commit -m "docs: 대화 품질 축 정의와 후속 런타임 카드 A/B 설계"
```

---

## 스펙 대조 (self-review)

| 스펙 요구 | 담은 곳 |
| --- | --- |
| 저장소 실사 6항목 | 「저장소 실사」 표 (전부 확인 완료) |
| `#342` 오인용 금지 | Global Constraints. **주의: 현재 다른 워크트리의 미커밋 `field_harvest` 가 `#342` 를 인용하고 있다 — 이 카드 밖이지만 사용자에게 보고했다** |
| 별도 멀티턴 스키마 | Task 1 |
| 케이스 12~20건 · 열 가지 모양 | Task 2 표 |
| 세 축 정의 0~2 · N/A | Task 3 |
| 총점 금지 · 게이트/벡터 | Task 3 `derive_usability`, Task 8 리포트 테스트 |
| `dead_end` 는 파생 진단 | Task 8 |
| `used_state` 를 점수로 안 씀 · 네 칸 사실 기록 | Task 3 `StateAudit` |
| 응답 시점 스냅샷 | Task 5 |
| 프로덕션 로깅 불변 · 원문 질의 미기록 | Task 5 |
| 결정적 테스트 · 가짜 모델 | Task 1·3·4·5·6·7·8 전부 |
| `#277` 동결 불변 테스트 | Task 2 |
| 지표·게이트로 승격 금지 | Task 8 (`not_calibrated`), `__init__.py` docstring |
| 후속 카드 A/B 문서만 | Task 9 |
| 런타임 불변식 유지 | Global Constraints (런타임 파일을 아무 태스크도 수정하지 않는다) |

**전후 비교 요구는 스펙에 없고 사용자가 더한 것이다** — Global Constraints 의 여섯과 Task 8 의 비교 거부 로직이 그것을 받는다.

## 남은 위험

- **바닥 효과.** `context_continuity`·`repair_success` 는 before 가 전부 0 이라 전후 비교의 검정력이 `response_mode_fit` 과 다르다. 리포트가 열 이름을 갈라 적는다
- **κ 를 baseline 에서 못 얻는다.** 앵커에서만 온다 — Task 7 의 양방향 테스트가 그 조건을 강제한다
- **`ASK` 계약 미정.** 케이스가 상호작용 모드로 적혀 있어 계약 결정에 안 흔들리지만, after 랩을 돌리기 전에 결정이 나 있어야 `expected_mode` 를 상태로 옮길 수 있다
- **실제 랩 실행은 이 계획 밖이다.** 유료 호출이라 하네스 완성 후 사람이 승인한다
