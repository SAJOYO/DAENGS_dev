# 기록된 산책과 `unmeasured` 고지 구현 계획 (D-072)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 "나루가 얼마나 걸었어?"라고 물었을 때, 기록이 있으면 기록으로 답하고 **없으면 왜 못 재는지를 고정 문장으로 밝힌다** — 거리를 추정하지 않고.

**Architecture:** 새 능력을 만들지 않는다. `care_log`(#344)·`vet_spend`(#353)가 두 번 검증한 "신뢰된 기록 → General payload 컨텍스트" 패턴을 **세 번째로** 적용해 `WALK_ACTIVITY` 를 싣고, 모델은 `GeneralAnswer.unmeasured` 마커만 세우며, 사용자에게 나가는 고지 문장은 `redirects.py` 의 제품 문장을 **어댑터가 코드로** 붙인다. 라우터·`CapabilityName`·좌표 계약은 한 줄도 안 바뀐다.

**Tech Stack:** Python 3.12 · uv · FastAPI · SQLAlchemy 2.0 async · pydantic v2 · pytest

**Spec:** 이 문서의 「설계 근거」 절이 스펙이다. 상위 결정은 `docs/decisions.md` 의 **D-051 ⑤**(지역명은 좌표가 아니다 · Option B 디스클로저)이고, 이 카드는 그 장치의 **조건부 판본**을 General 에 처음 들이는 것이라 **D-072 로 기록한다**(Task 7).

---

## Global Constraints

아래는 전 태스크에 걸린다. 하나라도 어기면 그 태스크는 spec ❌ 다.

1. **D-051 을 지킨다.** 지명을 좌표로 바꾸지 않는다. geocoder 를 부르지 않는다. 모델 출력에서 좌표가 오지 않는다. `daengs_journey` 를 부르지 않는다. **경로를 추정하지 않는다.**
2. **좌표는 이 카드에서 한 칸도 안 넘어간다.** `WalkActivityContext` 에는 위경도·폴리라인·지점 목록이 없다. 넘어가는 것은 건수·합계 거리(m)·합계 이동시간(s)·마지막 시작 시각뿐이다.
3. **`CapabilityName` 을 넓히지 않는다.** `semantic.py` 의 `ExecuteName`·`_POLICY`·프롬프트 버전을 **안 건드린다** → 80건 골드 회귀 불필요.
4. **`_SAFETY_PROMPT` 의 기본 본문을 고치지 않는다.** 새 규칙은 **더해지는 문단**이고, `WALK_ACTIVITY` 가 없는 요청의 프롬프트는 **바이트 단위로 지금과 같아야 한다** — D-057 ③ 의 84건 승인 계보가 거기 걸려 있고 `tests/test_assistant_care_log.py` 가 그것을 붙잡는다.
5. **프롬프트 버전은 접미사 `-walk`** 로 만든다. 새 상수 네 개를 또 만들지 않는다 (`-conv` 가 같은 이유로 접미사다). 붙는 순서는 **`<base>` → `-walk` → `-conv`** 로 고정한다.
6. **고지 문장은 모델이 쓰지 않는다.** `redirects.py` 에 있고 어댑터가 붙인다 (#278 의 규칙).
7. **마커를 놓쳐도 숫자를 지어내지 않는다.** "추정 금지" 규칙은 `unmeasured` 와 **독립으로** 걸린다.
8. **유료 호출 금지** — 사람이 명시 승인하기 전까지. 자동 테스트는 가짜 모델만 쓴다 (`tests/fakes.py`).
9. 서버 PC 명령 · 머지 · 배포 · 프로덕션 데이터 금지. 개발 PC 작업이다.
10. Python 3.12 고정. 의존성 추가는 `uv add` 로만. **이 카드는 새 의존성이 필요 없다.**

---

## 설계 근거 (스펙)

### 왜 마커인가

D-051 ⑤ 가 조건부 고지를 미룬 이유는 명시돼 있다 — *"「지역명이 있었나」를 판정하려면 **라우터 분류를 하나 더 만들어야 하는데** 그것이 바로 이 카드가 미룬 것"*. 비용은 새 분류기였지 조건부 자체가 아니었다.

여기서는 새 분류기가 없다. General 모델은 이미 돌고 이미 구조화 출력을 낸다. `GeneralAnswer.axes` 가 **똑같은 모양의 선례**다 — 모델이 고르고(`#: 코드가 채우지 않는다 — 모델이 고른 것만 그대로 나간다`), 사용자에게 보이는 문장은 코드가 짓는다.

무조건을 고르지 않은 이유: Place·vet_contact 의 고지가 무조건일 수 있는 것은 **능력 자체가 범위**이기 때문이다(Place 결과는 전부 위치 검색이다). General 은 급여량부터 진료비까지 다 들어오는 catch-all 이라 깎을 범위가 없고, 무조건은 "무조건"이 아니라 "아무 데나"가 된다. 매번 나오는 줄은 사용자가 건너뛴다 — **건너뛰는 줄은 안 나오는 줄과 값어치가 같다.**

### 마커의 약점과 그 대가

누락되면 사용자는 이유를 못 듣는다. 그 약점을 **재는 것**으로 갚는다 — Task 7 이 `conversation_quality` 케이스를 넣어 누락률을 숫자로 만든다. **재지 않는 조건부 고지는 D-051 이 옳게 거부한 것이고, 재는 조건부 고지는 다른 물건이다.** 누락률이 나쁘면 무조건으로 내리는 것은 `if answer.unmeasured:` 를 `if payload.walk_activity is None:` 으로 바꾸는 한 줄이다.

### 이 카드에 **없는** 것과 그 이유

| 뺀 것 | 왜 |
| --- | --- |
| 지명 지오코딩 · journey 호출 · 구간 추정 | D-051. 이 카드는 그 결정을 **지키는** 카드다 |
| `_SAFETY_PROMPT` 의 `briefly (3 to 5 sentences)` 하한 수정 | 기본 본문을 고치면 v9 가 되고 네 조합 전부의 버전이 오르며 **D-057 ③ 의 84건 승인 계보가 끊긴다.** 유료 재실행과 사람 승인이 붙는 별도 카드다 |
| 어제·오늘 말고 **기간** 질의 ("이번 주 얼마나 걸었어") | v1 은 하루다. `care_log` 와 같은 범위로 시작한다 |
| `walk_analysis` 의 stop_count · 모션 이벤트 · 캡슐 | 답 문장에 필요 없다. `CareLogContext` 가 `note` 를 뺀 것과 같은 규칙 |

---

## File Structure

```
backend/src/daengs_backend/
  orchestration/redirects.py            수정 — 고지 문장 1개 (제품 문장)
  orchestration/contracts.py            수정 — WalkActivityContext + GeneralPayload.walk_activity
  orchestration/planner.py              수정 — _walk_activity_context() 화이트리스트 복사기 + payload 조립 3줄
  orchestration/adapters/general.py     수정 — _WALK_ACTIVITY_RULE · WALK_ACTIVITY 블록 · `-walk` 접미사 ·
                                               GeneralAnswer.unmeasured · 어댑터 OK 경로에서 고지 붙이기
  repositories/walk.py                  수정 — activity_for_pet_between()  (count_for_pet_between 의 형제)
  services/walk_activity_context.py     신규 — care_log_context.py 를 본뜬 resolve()
  routers/assistant.py                  수정 — _with_dog_context 안에서 같은 세션으로 한 줄

backend/tests/
  test_assistant_walk_activity.py       신규 — test_assistant_care_log.py 와 같은 평면
  test_orchestration_general_fallback.py 수정 — 마커·고지·접미사

backend/evals/conversation_quality/
  cases_v1.jsonl                        수정 — 이동량 케이스 3건

docs/decisions.md                       수정 — D-072
```

---

### Task 0: 브랜치와 Draft PR

**Files:** 없음 (git · gh 작업)

- [ ] **Step 1: 워크트리 확인**

이 계획서는 `origin/dev` 에서 판 워크트리 `walk-unmeasured`(브랜치 `feat/walk-activity-unmeasured`)에 이미 있다. 다른 워크트리에서 브랜치를 갈아타지 않는다.

```bash
git log --oneline -1          # 5d745235 여야 한다
git rev-list --left-right --count origin/dev...HEAD    # 0  0 여야 한다
```

- [ ] **Step 2: 베이스라인 테스트**

```bash
cd backend && uv sync --extra place --extra agent && uv run pytest -q
```
Expected: 전부 통과. **실패가 있으면 여기서 멈추고 보고한다** — 더러운 베이스라인은 이후 모든 실패를 모호하게 만든다.

- [ ] **Step 3: 빈 착수 커밋과 Draft PR**

```bash
git commit --allow-empty -m "chore: 착수 — 기록된 산책과 unmeasured 고지 (D-072)"
git push -u origin feat/walk-activity-unmeasured
```

PR 본문은 `.github/PULL_REQUEST_TEMPLATE.md` 의 `##` 제목을 **그대로 두고** 내용만 채운다. `## 배포 영향` 은 "없음 — 코드만 바뀜"에 체크한다.

---

### Task 1: 고지 문장과 계약

**Files:**
- Modify: `backend/src/daengs_backend/orchestration/redirects.py`
- Modify: `backend/src/daengs_backend/orchestration/contracts.py`
- Test: `backend/tests/test_assistant_walk_activity.py` (신규)

**Interfaces:**
- Consumes: 없음 (이 카드의 첫 태스크)
- Produces: `redirects.DISTANCE_FROM_RECORDED_WALKS_ONLY: str` · `contracts.WalkActivityContext` · `contracts.GeneralPayload.walk_activity: WalkActivityContext | None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_assistant_walk_activity.py` 를 새로 만든다:

```python
"""D-072 — 기록된 산책이 비서 프롬프트까지 가는 길, 그리고 **못 잴 때 무엇을 말하는가**.

DB 는 안 씁니다. `test_assistant_care_log.py` 와 같은 꼴로 리포지토리를 가짜로 바꿉니다.
여기서 보는 것은 **규칙**입니다 — 좌표가 한 칸도 안 넘어가는가, 측정 안 된 산책이 합계에
안 섞이는가, 기록이 없는 요청의 프롬프트가 이 카드 전과 글자까지 같은가.
"""

import pytest
from pydantic import ValidationError

from daengs_backend.orchestration.contracts import GeneralPayload, WalkActivityContext
from daengs_backend.orchestration.redirects import DISTANCE_FROM_RECORDED_WALKS_ONLY


def test_disclosure_names_the_record_and_refuses_the_described_route() -> None:
    """D-051 ⑤ 의 고지와 같은 성질 — 못 하는 사실과 **그 이유**를 같이 말한다."""
    assert "기록된 산책" in DISTANCE_FROM_RECORDED_WALKS_ONLY
    assert "말씀" in DISTANCE_FROM_RECORDED_WALKS_ONLY
    # 되묻는 문장으로 읽히면 안 된다 — `VET_CONTACT_LOCATION_UNKNOWN` 과 같은 규칙
    assert "?" not in DISTANCE_FROM_RECORDED_WALKS_ONLY


def test_walk_activity_context_carries_no_coordinate() -> None:
    """좌표는 이 카드에서 한 칸도 안 넘어간다 (Global Constraint 2)."""
    for forbidden in ("lat", "lon", "lng", "polyline", "points", "path"):
        assert forbidden not in WalkActivityContext.model_fields, forbidden


def test_measured_walks_never_exceed_recorded_walks() -> None:
    """측정된 산책이 기록된 산책보다 많을 수 없다 — 조인이 중복 합산하면 여기서 걸린다."""
    with pytest.raises(ValidationError):
        WalkActivityContext(
            day="2026-09-12", walk_count=1, measured_walk_count=2,
            distance_m=100, moving_s=60,
        )


def test_general_payload_takes_walk_activity_and_life_does_not() -> None:
    """`care_log` 와 같은 규칙 — 폴백에만 간다."""
    from daengs_backend.orchestration.contracts import LifePayload

    activity = WalkActivityContext(
        day="2026-09-12", walk_count=2, measured_walk_count=1,
        distance_m=1_200, moving_s=900, last_started_at="08:30",
    )
    assert GeneralPayload(question="q", walk_activity=activity).walk_activity == activity
    with pytest.raises(ValidationError):
        LifePayload(question="q", walk_activity=activity)
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd backend && uv run pytest tests/test_assistant_walk_activity.py -q
```
Expected: FAIL — `ImportError: cannot import name 'DISTANCE_FROM_RECORDED_WALKS_ONLY'`

- [ ] **Step 3: 고지 문장을 더한다**

`redirects.py` 의 `VET_CONTACT_CURRENT_LOCATION_FRAME` **아래**에 넣는다:

```python
#: 이동 거리를 못 재는 이유 (D-072). **왜 못 하는지까지 말한다** — 댕스는 기록된 산책에서만
#: 거리를 내고, 그것은 지명을 좌표로 바꾸지 않기로 한 결정(D-051 ⑤)의 결과다. 그 결정을
#: 이 한 줄이 사용자에게 갚는다. `VET_CONTACT_HOURS_UNKNOWN` 과 같은 성질이다.
#:
#: **조건은 `GeneralAnswer.unmeasured` 다.** Place · vet_contact 의 고지가 무조건인 것은
#: 능력 자체가 범위여서인데, General 은 catch-all 이라 깎을 범위가 없다. 대신 누락률을
#: `evals/conversation_quality` 가 잰다 — 재지 않는 조건부 고지는 D-051 이 거부한 것이다.
DISTANCE_FROM_RECORDED_WALKS_ONLY = (
    "이동 거리와 시간은 앱에 기록된 산책에서만 계산해요. "
    "말씀해 주신 경로는 기록에 없어서 재어 드릴 수 없어요."
)
```

`__all__` 에 `"DISTANCE_FROM_RECORDED_WALKS_ONLY"` 를 알파벳 순서에 맞게 더한다.

- [ ] **Step 4: 계약을 더한다**

`contracts.py` 의 `CareLogContext` **바로 아래**에 넣는다 (`_CLOCK_PATTERN` 은 이미 그 위에 있다):

```python
class WalkActivityContext(ContractModel):
    """오늘 앱이 **실제로 기록한** 산책: 건수, 그중 측정이 끝난 건수, 그 합계 거리와 이동 시간.

    `CareLogContext` 의 형제이고 규칙이 같다 — 소유권을 확인해 읽고, 좁혀서 넘기고, 없으면
    None. 다른 것은 **무엇을 빼느냐**다.

    **좌표가 한 칸도 없다.** 위경도 · 폴리라인 · 지점 목록이 여기 있으면 D-051 이 "지명은
    좌표가 아니다" 로 막아 둔 것을 뒷문으로 여는 셈이 된다 — 모델이 경로를 받으면 그것으로
    다른 경로를 추정한다. 답 문장에 필요한 것은 합계뿐이다.

    **`walk_count` 와 `measured_walk_count` 가 따로인 것이 요점이다.** 한 산책에 분석 행이
    여러 개 달릴 수 있고(`walk_analyses` 의 유니크 제약이 6칸이다), 봉인이 안 끝난 산책도
    있다. 합계는 **측정이 끝난 것만** 더한 값이고, 둘이 다르면 답이 그 사실을 말한다 —
    "3건 중 2건만 계산됐어요" 는 참이지만 "3건에 1.2km" 는 거짓이다.

    **거리를 km 로 미리 나누지 않는다.** 반올림은 답을 쓰는 자리에서 하고, 계약은 원값을
    나른다. `CareLogContext` 가 시각을 타임스탬프가 아니라 `HH:MM` 로 나르는 것과 반대
    방향처럼 보이지만 이유는 같다 — 소비자가 필요로 하는 모양으로만 준다.

    기록이 하나도 없는 날은 여기 안 온다(resolver 가 None 을 낸다): 빈 기록은 "안 걸었다"
    가 아니라 "이 기능을 안 쓴다" 일 수 있고, 프롬프트가 둘 중 어느 쪽도 말하면 안 된다.
    """

    day: date
    walk_count: int = Field(ge=0, le=200)
    measured_walk_count: int = Field(ge=0, le=200)
    #: 측정이 끝난 산책의 합계 거리(m). 하루 500km 를 넘는 값은 기록이 아니라 사고다.
    distance_m: int = Field(ge=0, le=500_000)
    #: 같은 산책들의 합계 이동 시간(s). 하루를 넘을 수 없다.
    moving_s: int = Field(ge=0, le=86_400)
    #: 마지막 산책이 시작된 시각. `CareLogContext` 와 같은 `HH:MM`(서울)이고 타임스탬프가 아니다.
    last_started_at: str | None = Field(default=None, pattern=_CLOCK_PATTERN)

    @model_validator(mode="after")
    def measured_never_exceeds_recorded(self) -> WalkActivityContext:
        if self.measured_walk_count > self.walk_count:
            raise ValueError("measured_walk_count cannot exceed walk_count")
        return self
```

`GeneralPayload` 에 칸을 더한다 (`vet_spend` 아래, `conversation` 위):

```python
    #: 오늘 기록된 산책 (D-072). `care_log`·`vet_spend` 와 같은 규칙 — 폴백에만 오고,
    #: Life 의 조례·보조금 문서는 오늘 걸은 거리로 달라지지 않는다. None 이면 프롬프트가
    #: 이 카드 전과 한 글자도 다르지 않다.
    walk_activity: WalkActivityContext | None = None
```

`GeneralPayload` 독스트링에 한 문단을 더한다:

```
    ``walk_activity`` (D-072) 는 오늘 기록된 산책의 합계다. 여기 **좌표가 없는 것이 설계**이고,
    이유는 `WalkActivityContext` 독스트링에 있다.
```

`__all__` 에 `"WalkActivityContext"` 를 알파벳 순서에 맞게 더한다.

- [ ] **Step 5: 통과를 확인한다**

```bash
cd backend && uv run pytest tests/test_assistant_walk_activity.py -q
```
Expected: PASS (4개)

- [ ] **Step 6: 회귀를 확인한다**

```bash
cd backend && uv run pytest tests/test_orchestration_contracts.py tests/test_assistant_care_log.py -q
```
Expected: PASS. **`test_capability_names_have_exactly_three_copies_and_they_agree` 가 여기서 통과해야 한다** — 이 카드는 `CapabilityName` 을 안 넓히므로 통과가 정상이고, 실패하면 범위를 벗어난 것이다.

- [ ] **Step 7: 커밋**

```bash
git add backend/src/daengs_backend/orchestration/redirects.py \
        backend/src/daengs_backend/orchestration/contracts.py \
        backend/tests/test_assistant_walk_activity.py
git commit -m "feat: 기록된 산책 계약과 못 잴 때의 고지 문장 (D-072)"
```

---

### Task 2: 리포지토리 조회

**Files:**
- Modify: `backend/src/daengs_backend/repositories/walk.py` (`count_for_pet_between` 바로 아래)
- Test: `backend/tests/test_assistant_walk_activity.py` (Task 1 에서 만든 파일에 추가)

**Interfaces:**
- Consumes: Task 1 의 `WalkActivityContext` (모양의 정의)
- Produces:
  ```python
  async def activity_for_pet_between(
      session: AsyncSession, pet_id: uuid.UUID, start: datetime, end: datetime,
  ) -> WalkActivitySums
  ```
  ```python
  @dataclass(frozen=True)
  class WalkActivitySums:
      walk_count: int
      measured_walk_count: int
      distance_m: int
      moving_s: int
      last_started_at: datetime | None
  ```

**⚠️ 이 태스크의 함정 (반드시 읽을 것):** `walk_analyses` 의 유니크 제약은 `(walk_id, input_fingerprint, facts_record_version, calculation_version, receipt_version, observation_version)` 6칸이다 — **한 산책에 분석 행이 여러 개 달린다.** `walks JOIN walk_analyses` 에 `SUM` 을 걸면 **거리가 배로 불어난다.** 정본을 가리키는 것은 `activity_walk_heads` 이고(PK 가 `walk_id`, `analysis_id` 가 unique), `services/walk.py` 의 finalize 경로 네 곳이 전부 `activity.record_walk` 로 그것을 세운다. **그 표를 경유해서만 더한다.**

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`test_assistant_walk_activity.py` 에 추가한다:

```python
def test_sums_join_through_the_head_so_one_walk_counts_once() -> None:
    """한 산책에 분석 행이 둘이어도 거리는 한 번만 더해진다.

    `walk_analyses` 는 (walk_id, fingerprint, 버전 4개) 로 유니크라 한 산책에 여러 세대가
    쌓인다. `activity_walk_heads` 를 경유하지 않고 더하면 거리가 배가 된다 — 이 테스트가
    그 조인을 붙잡는다. SQL 자체는 Step 5 의 db_or_skip 테스트가 본다.
    """
    from daengs_backend.repositories.walk import activity_for_pet_between

    source = inspect.getsource(activity_for_pet_between)
    assert "ActivityWalkHead" in source, "정본 분석은 activity_walk_heads 가 가리킨다"


def test_unmeasured_walks_count_but_do_not_add_distance() -> None:
    """봉인 안 된 산책은 건수에는 들어가고 합계에는 안 들어간다."""
    sums = WalkActivitySums(
        walk_count=3, measured_walk_count=2, distance_m=1_200, moving_s=900,
        last_started_at=datetime(2026, 9, 12, 8, 30, tzinfo=UTC),
    )
    assert sums.walk_count > sums.measured_walk_count
    # 계약이 이 조합을 받아들여야 "3건 중 2건만 계산됐어요" 를 말할 수 있다
    WalkActivityContext(
        day="2026-09-12", walk_count=sums.walk_count,
        measured_walk_count=sums.measured_walk_count,
        distance_m=sums.distance_m, moving_s=sums.moving_s,
    )
```

파일 맨 위 import 에 `inspect`, `from datetime import UTC, datetime`, 그리고 `from daengs_backend.repositories.walk import WalkActivitySums` 를 더한다.

- [ ] **Step 2: 실패를 확인한다**

```bash
cd backend && uv run pytest tests/test_assistant_walk_activity.py -q
```
Expected: FAIL — `ImportError: cannot import name 'WalkActivitySums'`

- [ ] **Step 3: 조회를 구현한다**

`repositories/walk.py` 의 `count_for_pet_between` 바로 아래에 넣는다. 파일 상단 import 에 `from dataclasses import dataclass` 와 `from daengs_backend.models.activity import ActivityWalkHead` 를 더한다.

```python
@dataclass(frozen=True)
class WalkActivitySums:
    """`activity_for_pet_between` 이 돌려주는 것. 건수와 합계가 **따로**인 이유는
    `WalkActivityContext` 독스트링에 있다."""

    walk_count: int
    measured_walk_count: int
    distance_m: int
    moving_s: int
    last_started_at: datetime | None


async def activity_for_pet_between(
    session: AsyncSession,
    pet_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> WalkActivitySums:
    """그 아이의 산책 건수와, **측정이 끝난 것만의** 합계 거리·이동 시간 (D-072).

    **소유자 조건을 안 겁니다** — `count_for_pet_between` 과 같은 이유입니다(docs/co-care.md
    §2). 부르는 쪽이 이미 접근 권한을 확인했고, 여기서 사람으로 다시 거르면 다른 보호자가
    다녀온 산책만 빠집니다.

    **`activity_walk_heads` 를 경유하는 것이 이 함수의 전부입니다.** `walk_analyses` 는 한
    산책에 여러 세대가 쌓이는 표라(유니크 제약이 6칸), 거기 바로 `SUM` 을 걸면 거리가
    배로 불어납니다. head 는 `walk_id` 가 PK 라 산책당 정확히 한 행이고, finalize 경로
    네 곳이 전부 그것을 세웁니다(`services/walk.py`).

    head 가 없는 산책은 **건수에는 들어가고 합계에는 안 들어갑니다.** 봉인이 안 끝난 것을
    0m 로 더하면 "걸었는데 0km" 가 되고, 건수에서까지 빼면 "안 걸었다" 가 됩니다. 둘 다
    거짓이라 두 수를 따로 냅니다.
    """
    walked = (
        select(
            func.count(func.distinct(Walk.id)).label("walk_count"),
            func.max(Walk.started_at).label("last_started_at"),
        )
        .join(WalkPet, WalkPet.walk_id == Walk.id)
        .where(
            WalkPet.pet_id == pet_id,
            Walk.started_at >= start,
            Walk.started_at < end,
        )
    )
    measured = (
        select(
            func.count(func.distinct(Walk.id)).label("measured_walk_count"),
            func.coalesce(func.sum(WalkAnalysis.moving_distance_m), 0).label("distance_m"),
            func.coalesce(func.sum(WalkAnalysis.moving_s), 0).label("moving_s"),
        )
        .join(WalkPet, WalkPet.walk_id == Walk.id)
        .join(ActivityWalkHead, ActivityWalkHead.walk_id == Walk.id)
        .join(WalkAnalysis, WalkAnalysis.id == ActivityWalkHead.analysis_id)
        .where(
            WalkPet.pet_id == pet_id,
            Walk.started_at >= start,
            Walk.started_at < end,
        )
    )
    walked_row = (await session.execute(walked)).one()
    measured_row = (await session.execute(measured)).one()
    return WalkActivitySums(
        walk_count=int(walked_row.walk_count or 0),
        measured_walk_count=int(measured_row.measured_walk_count or 0),
        distance_m=int(measured_row.distance_m or 0),
        moving_s=int(measured_row.moving_s or 0),
        last_started_at=walked_row.last_started_at,
    )
```

`__all__` 이 이 파일에 있으면 두 이름을 더한다.

- [ ] **Step 4: 통과를 확인한다**

```bash
cd backend && uv run pytest tests/test_assistant_walk_activity.py -q
```
Expected: PASS (6개)

- [ ] **Step 5: SQL 을 실물 DB 로 한 번 본다 (있을 때만)**

`tests/conftest.py` 에 `db_or_skip` fixture 가 있다. 그것을 받는 테스트를 하나 더해, 같은 산책에 분석 행 둘을 넣고 거리가 **배가 아닌지** 본다. DB 가 없으면 skip 되고 그것이 정상이다.

**`conftest.py` 에 `db_or_skip` 이 있을 때만 한다.** 없거나 DB 가 안 붙으면 skip 되고 그것이 정상이다 — 없는 픽스처를 새로 만드는 것은 이 카드의 범위가 아니며, 건너뛰었으면 그 사실을 보고서에 적는다.

세우는 것: 산책 1건 · 그 산책에 `walk_analyses` 2행(거리 600 과 700, `input_fingerprint` 가 다르다) · `activity_walk_heads` 1행(700 짜리를 가리킨다).

```python
@pytest.mark.usefixtures("db_or_skip")
async def test_분석_세대가_둘이어도_거리가_배가_되지_않는다(db_session) -> None:
    """head 를 안 거치면 1,300 이 나온다. 거쳐야 700 이다."""
    sums = await activity_for_pet_between(
        db_session, pet_id, start=_day_start, end=_day_end,
    )
    assert sums.walk_count == 1
    assert sums.measured_walk_count == 1
    assert sums.distance_m == 700          # 600 + 700 = 1,300 이 아니다
```

행을 넣는 방법은 이 저장소의 다른 `db_or_skip` 테스트를 그대로 따른다. `pet_id`·`_day_start`·`_day_end` 는 그 테스트가 만든 값이다.

- [ ] **Step 6: 커밋**

```bash
git add backend/src/daengs_backend/repositories/walk.py backend/tests/test_assistant_walk_activity.py
git commit -m "feat: 기록된 산책의 건수와 측정 합계를 head 경유로 읽는다 (D-072)"
```

---

### Task 3: 서비스 resolver

**Files:**
- Create: `backend/src/daengs_backend/services/walk_activity_context.py`
- Test: `backend/tests/test_assistant_walk_activity.py` (추가)

**Interfaces:**
- Consumes: Task 2 의 `walk_repo.activity_for_pet_between` · `WalkActivitySums`
- Produces:
  ```python
  async def resolve(
      session: AsyncSession, app_user_id: uuid.UUID, active_dog_id: str,
      *, today: date | None = None,
  ) -> dict[str, object] | None
  ```
  돌려주는 키: `day`(`YYYY-MM-DD`) · `walk_count` · `measured_walk_count` · `distance_m` · `moving_s` · `last_started_at`(`HH:MM`, 없으면 키 자체가 없음)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

픽스처는 `tests/test_assistant_care_log.py:73-115` 의 `store`·`pet` 과 같은 모양이고, `care` 픽스처 자리에 산책용 `walks` 가 들어간다. 파일 상단에 상수와 픽스처를 먼저 둔다:

```python
OWNER = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
TODAY = date(2026, 9, 12)
SEOUL = ZoneInfo("Asia/Seoul")


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    return s


@pytest.fixture
def pet(store: Store) -> FakePet:
    p = FakePet(app_user_id=OWNER, name="네옹", breed="dog_pug")
    store.pets.append(p)
    return p


@pytest.fixture
def walks(monkeypatch: pytest.MonkeyPatch) -> dict[str, WalkActivitySums]:
    """`activity_for_pet_between` 을 가짜로 바꾼다 — care_log 테스트의 `care` 와 같은 꼴."""
    box = {"sums": WalkActivitySums(0, 0, 0, 0, None)}

    async def activity_for_pet_between(session, pet_id, start, end):
        return box["sums"]

    monkeypatch.setattr(walk_repo, "activity_for_pet_between", activity_for_pet_between)
    return box


async def _resolve(pet_id, *, today: date = TODAY):
    return await walk_activity_context.resolve(object(), OWNER, str(pet_id), today=today)
```

그 아래에 테스트 다섯:

```python
async def test_기록이_없는_날은_None(pet, walks) -> None:
    """빈 날은 "안 걸었다" 가 아니라 "산책 기록을 안 쓴다" 일 수 있다."""
    assert await _resolve(pet.id) is None


async def test_uuid_가_아니면_None(walks) -> None:
    assert await _resolve("uuid 아님") is None


async def test_남의_강아지는_None(walks) -> None:
    assert await _resolve(uuid.uuid4()) is None


async def test_DB_오류는_삼키고_None(pet, walks, monkeypatch, caplog) -> None:
    """`walk_analyses`·`activity_walk_heads` 가 서버에 아직 없을 수 있다. 그때 비서가
    죽으면 안 된다 — 기록 없이, 이 카드 전과 똑같이 답한다."""
    async def boom(*args, **kwargs):
        raise OperationalError(
            "SELECT", {}, Exception('relation "activity_walk_heads" does not exist')
        )

    monkeypatch.setattr(walk_repo, "activity_for_pet_between", boom)
    with caplog.at_level("WARNING"):
        assert await _resolve(pet.id) is None
    assert "산책" in caplog.text


async def test_하루를_건수와_합계와_시각으로_좁힌다(pet, walks) -> None:
    """넘어가는 것은 합계와 `HH:MM` 뿐 — 타임스탬프도 좌표도 아니다."""
    walks["sums"] = WalkActivitySums(
        walk_count=3, measured_walk_count=2, distance_m=1_240, moving_s=1_500,
        last_started_at=datetime(2026, 9, 12, 8, 30, tzinfo=SEOUL),
    )
    assert await _resolve(pet.id) == {
        "day": "2026-09-12",
        "walk_count": 3,
        "measured_walk_count": 2,
        "distance_m": 1_240,
        "moving_s": 1_500,
        "last_started_at": "08:30",
    }
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd backend && uv run pytest tests/test_assistant_walk_activity.py -q
```
Expected: FAIL — `ModuleNotFoundError: daengs_backend.services.walk_activity_context`

- [ ] **Step 3: 서비스를 만든다**

`services/care_log_context.py`(83줄)를 열어 **구조를 그대로 따른다**: 모듈 독스트링에 "무엇이 안 넘어가는가"를 적고, `uuid.UUID` 변환 실패·`PetNotFoundError`·`SQLAlchemyError` 를 전부 `None` 으로 받고, 아무 값도 없으면 `None` 을 낸다. 날짜 경계는 `care_service.DAY_TIMEZONE`(서울)을 그대로 쓴다 — **두 요약이 다른 하루를 말하면 안 된다.**

```python
"""활성 반려견 → 비서가 받아도 되는 오늘의 산책 요약 (D-072).

`services/care_log_context.py` 의 형제이고 규칙이 같습니다 — **소유권을 확인해 읽고, 좁혀서
넘기고, 못 채우면 조용히 None**. 넘어가는 모양은 `orchestration.contracts` 의
`WalkActivityContext` 입니다.

**좌표는 한 칸도 안 넘깁니다.** 이 표에는 GPS 청크가 있지만 넘기는 것은 합계뿐입니다 —
이유는 `WalkActivityContext` 독스트링에 있고, 한 줄로 줄이면 D-051 입니다.

**하루 경계를 케어 로그와 같이 씁니다** (`care_service.DAY_TIMEZONE`). 두 요약이 한 프롬프트에
같이 실리는데 서로 다른 "오늘" 을 말하면, 모델이 그것을 두 개의 사실로 읽습니다.

**빈 날은 None 입니다.** 기록이 0건인 것은 "안 걸었다" 가 아니라 "산책 기록을 안 쓴다" 일 수
있습니다. 어느 쪽인지 모르는 채로 빈 블록을 실으면 모델이 둘 중 하나로 읽습니다.

**DB 오류도 None 입니다.** `care_log_context` 와 같습니다 — 기록을 못 읽었다고 답할 수 있는
질문을 실패시키지 않습니다.
"""
```

- [ ] **Step 4: 통과를 확인한다**

```bash
cd backend && uv run pytest tests/test_assistant_walk_activity.py -q
```
Expected: PASS (10개)

- [ ] **Step 5: 커밋**

```bash
git add backend/src/daengs_backend/services/walk_activity_context.py backend/tests/test_assistant_walk_activity.py
git commit -m "feat: 오늘의 산책 요약 resolver — 빈 날과 DB 오류는 조용히 None (D-072)"
```

---

### Task 4: 배선 (HTTP 경계 → planner → payload)

**Files:**
- Modify: `backend/src/daengs_backend/routers/assistant.py` (`_with_dog_context`)
- Modify: `backend/src/daengs_backend/orchestration/planner.py`
- Test: `backend/tests/test_assistant_walk_activity.py` (추가)

**Interfaces:**
- Consumes: Task 3 의 `walk_activity_context.resolve` · Task 1 의 `GeneralPayload.walk_activity`
- Produces: `context["walk_activity"]` (신뢰된 구조화 컨텍스트) · `planner._walk_activity_context(context) -> dict | None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_planner_drops_a_malformed_field_not_the_request() -> None:
    """`_care_log_context` 와 같은 규칙 — 모양이 틀린 칸은 그 칸만 버린다."""
    from daengs_backend.orchestration.planner import _walk_activity_context

    assert _walk_activity_context({"walk_activity": "not a mapping"}) is None
    assert _walk_activity_context({}) is None
    resolved = _walk_activity_context({
        "walk_activity": {
            "day": "2026-09-12", "walk_count": 2, "measured_walk_count": 1,
            "distance_m": 1_200, "moving_s": 900, "last_started_at": "8:30",  # 잘못된 시각
        }
    })
    assert resolved is not None
    assert "last_started_at" not in resolved     # 그 칸만 빠진다
    assert resolved["walk_count"] == 2


def test_life_never_receives_the_walk_summary() -> None:
    """폴백에만 간다 — `care_log` 와 같다. planner 가 Life payload 에 안 얹는지 본다."""
    from daengs_backend.orchestration.planner import _payload_for

    context = {
        "walk_activity": {
            "day": "2026-09-12", "walk_count": 1, "measured_walk_count": 1,
            "distance_m": 1_200, "moving_s": 900,
        }
    }
    # `_payload_for` 는 capability 뒤가 전부 키워드 전용이다 (planner.py:272)
    assert "walk_activity" not in _payload_for("life", query="q", context=context)
    assert "walk_activity" in _payload_for("general", query="q", context=context)


def test_a_request_without_a_record_builds_the_same_prompt_as_before() -> None:
    """**이 카드의 가장 중요한 테스트.** 기록이 없는 요청의 프롬프트가 바이트로 같아야 한다.

    `tests/test_assistant_care_log.py` 가 v3 본문에 대해 하는 것과 같은 자리다 —
    D-057 ③ 의 승인 계보가 여기 걸려 있다 (Global Constraint 4).
    """
    from daengs_backend.orchestration.adapters.general import build_general_prompt

    payload = GeneralPayload(question="밥은 하루에 몇 번 줘야 해?")
    assert "WALK_ACTIVITY" not in build_general_prompt(payload)
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd backend && uv run pytest tests/test_assistant_walk_activity.py -q
```
Expected: FAIL — `ImportError: cannot import name '_walk_activity_context'`

- [ ] **Step 3: planner 의 화이트리스트 복사기를 더한다**

`planner.py` 의 `_care_log_context` **바로 아래**에, 그 함수를 본떠 `_walk_activity_context` 를 쓴다. 상수는 그 옆의 `_CARE_LOG_CLOCK` 을 재사용한다 (`HH:MM` 패턴이 같다). 범위 검사는 `WalkActivityContext` 의 것과 같은 수를 쓴다 — `walk_count`/`measured_walk_count` 는 `0..200`, `distance_m` 은 `0..500_000`, `moving_s` 는 `0..86_400`.

`_payload_for` 의 general 분기에서 `vet_spend` 다음, `conversation` 앞에 세 줄을 더한다:

```python
        # 오늘 기록된 산책도 폴백에만 간다 (D-072): "얼마나 걸었어" 는 일반 질문이고,
        # Life 의 조례는 그 답을 안 들고 있다.
        walk_activity = _walk_activity_context(context)
        if walk_activity is not None:
            payload["walk_activity"] = walk_activity
```

- [ ] **Step 4: HTTP 경계를 잇는다**

`routers/assistant.py` 상단에 import 를 더하고:

```python
from daengs_backend.services import walk_activity_context as walk_activity_context_service
```

`_with_dog_context` 의 `async with session_factory() as session:` 블록 안, `vet_spend` 조회 **다음**에:

```python
        walk_activity = await walk_activity_context_service.resolve(
            session, principal.app_user_id, active_dog_id
        )
```

그리고 그 아래 `resolved` 조립에:

```python
    if walk_activity is not None:
        resolved["walk_activity"] = walk_activity
```

`_with_dog_context` 독스트링에 한 문단을 더한다 — `care_log`·`vet_spend` 문단과 같은 꼴로, **세션을 하나 더 열지 않는다**는 이유를 적는다.

- [ ] **Step 5: 통과를 확인한다**

```bash
cd backend && uv run pytest tests/test_assistant_walk_activity.py tests/test_assistant_care_log.py -q
```
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/src/daengs_backend/routers/assistant.py \
        backend/src/daengs_backend/orchestration/planner.py \
        backend/tests/test_assistant_walk_activity.py
git commit -m "feat: 오늘의 산책 요약을 General payload 까지 잇는다 (D-072)"
```

---

### Task 5: 프롬프트 블록과 `-walk` 접미사

**Files:**
- Modify: `backend/src/daengs_backend/orchestration/adapters/general.py`
- Test: `backend/tests/test_assistant_walk_activity.py` (추가)

**Interfaces:**
- Consumes: Task 1 의 `GeneralPayload.walk_activity`
- Produces: `general._WALK_ACTIVITY_RULE` · `general_prompt_version()` 이 내는 `-walk` 접미사

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_walk_suffix_sits_between_the_base_and_conv() -> None:
    """순서를 고정한다 — `<base>` → `-walk` → `-conv` (Global Constraint 5)."""
    from daengs_backend.orchestration.adapters.general import general_prompt_version

    activity = WalkActivityContext(
        day="2026-09-12", walk_count=1, measured_walk_count=1,
        distance_m=1_200, moving_s=900,
    )
    assert general_prompt_version(
        GeneralPayload(question="q", walk_activity=activity)
    ) == "general-answer-ko-v8-walk"


def test_the_rule_forbids_estimating_from_a_described_route() -> None:
    """D-051 을 프롬프트에서 한 번 더 못 박는다 (Global Constraint 1)."""
    activity = WalkActivityContext(
        day="2026-09-12", walk_count=1, measured_walk_count=1,
        distance_m=1_200, moving_s=900,
    )
    prompt = build_general_prompt(GeneralPayload(question="q", walk_activity=activity))
    assert "WALK_ACTIVITY" in prompt
    assert "Never estimate the distance or the time from a route described in words" in prompt
    assert "from place names" in prompt


def test_the_rule_makes_the_two_counts_speakable() -> None:
    """측정 안 된 산책이 있으면 답이 그 사실을 말할 수 있어야 한다."""
    activity = WalkActivityContext(
        day="2026-09-12", walk_count=3, measured_walk_count=2,
        distance_m=1_240, moving_s=1_500,
    )
    prompt = build_general_prompt(GeneralPayload(question="q", walk_activity=activity))
    assert "walk_count is larger than measured_walk_count" in prompt
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd backend && uv run pytest tests/test_assistant_walk_activity.py -k walk_suffix -q
```
Expected: FAIL — 버전이 `general-answer-ko-v8` 로 나온다

- [ ] **Step 3: 규칙 문단을 더한다**

`_VET_SPEND_RULE` 바로 아래에 넣는다. **영문이다** — `_SAFETY_PROMPT`·`_CARE_LOG_RULE` 과 같은 언어여야 한다(v3 의 사람 결정).

```python
# D-072. `_CARE_LOG_RULE` 과 같은 결이고, 다른 것은 **못 잴 때 무엇을 하느냐** 한 문단이다.
# 고지 문장은 여기 없다 — 어댑터가 `redirects.DISTANCE_FROM_RECORDED_WALKS_ONLY` 를 붙인다.
# 모델이 그 문장을 쓰면 판본이 둘이 되고, 그것이 #278 이 막은 것이다.
_WALK_ACTIVITY_RULE = """WALK_ACTIVITY, when present, is what the app actually recorded for this dog's walks today: how many walks were recorded, how many of those have a finished measurement, the total measured distance in metres, the total measured moving time in seconds, and the clock time the last walk started, as HH:MM in Seoul time. Treat it as fact for questions like "how far did we walk today" or "how long was the walk". Report the distance and the time as they are; round only for readability and never convert a number you were not given. When walk_count is larger than measured_walk_count, say plainly that some recorded walks have no measurement yet and give the total for the ones that do — "3 recorded, 2 measured, 1.2 km" is true and "3 walks, 1.2 km" is not.

Set unmeasured to true when the question asks how far or how long THIS dog moved and WALK_ACTIVITY cannot answer it — it is absent, no walk has a measurement, or the trip the owner is describing is not what was recorded. Never estimate the distance or the time from a route described in words, from place names, from a count of stops, or from how long the owner says the trip took. A sentence explaining why the number is unavailable is added after your answer, so do not write that explanation yourself, do not apologise for it, and do not tell the owner to use a map app. You may still say which parts of a described trip the dog would not have walked at all, such as a stretch travelled by bus or train. When WALK_ACTIVITY is absent, say nothing about a walk record unless you are setting unmeasured."""
```

- [ ] **Step 4: 조립과 버전에 잇는다**

`build_general_prompt` 의 `rule_blocks` 에 (vet_spend 다음):

```python
    if payload.walk_activity is not None:
        rule_blocks.append(_WALK_ACTIVITY_RULE)
```

`context_lines` 에 (VET_RECENT 다음, CONVERSATION 앞 — **CONVERSATION 은 `USER_QUERY` 바로 앞이어야 한다**):

```python
    if payload.walk_activity is not None:
        walk_activity = payload.walk_activity.model_dump(mode="json", exclude_none=True)
        context_lines.append(
            f"WALK_ACTIVITY: {json.dumps(walk_activity, ensure_ascii=False, sort_keys=True)}"
        )
```

`general_prompt_version` 에 `-conv` **앞에** 접미사를 더한다:

```python
    if payload.walk_activity is not None:
        version = f"{version}-walk"
    if payload.conversation is not None:
        version = f"{version}-conv"
```

`-walk` 접미사 상수 주석을 `-conv` 주석 옆에 적는다 — 새 상수 네 개를 안 만드는 이유가 같다.

- [ ] **Step 5: 통과와 회귀를 확인한다**

```bash
cd backend && uv run pytest tests/test_assistant_walk_activity.py tests/test_assistant_care_log.py tests/test_orchestration_general_fallback.py -q
```
Expected: PASS. **`test_assistant_care_log.py` 가 반드시 통과해야 한다** — 기록 없는 요청의 프롬프트가 안 바뀌었다는 증거다.

- [ ] **Step 6: 커밋**

```bash
git add backend/src/daengs_backend/orchestration/adapters/general.py backend/tests/test_assistant_walk_activity.py
git commit -m "feat: WALK_ACTIVITY 규칙과 -walk 접미사 — 경로 추정을 프롬프트에서 막는다 (D-072)"
```

---

### Task 6: `unmeasured` 마커와 고지 붙이기

**Files:**
- Modify: `backend/src/daengs_backend/orchestration/adapters/general.py` (`GeneralAnswer`, `GeneralCapabilityAdapter.run`)
- Test: `backend/tests/test_orchestration_general_fallback.py` · `backend/tests/test_assistant_walk_activity.py`

**Interfaces:**
- Consumes: Task 1 의 `DISTANCE_FROM_RECORDED_WALKS_ONLY` · Task 5 의 `_WALK_ACTIVITY_RULE`
- Produces: `GeneralAnswer.unmeasured: Literal[True] | None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_unmeasured_belongs_to_an_answer_only() -> None:
    """거절·되묻기는 이 마커를 못 든다 — 계약이 막는다.

    `axes` 가 되묻기에만 붙는 것과 같은 규칙이다. 거절에 붙으면 리다이렉트 문구 뒤에
    고지가 또 붙어 같은 상황이 두 문장으로 나간다.
    """
    from daengs_backend.orchestration.adapters.general import validate_general_answer

    assert validate_general_answer(
        {"kind": "answer", "text": "기록이 없어요.", "unmeasured": True}
    ) is not None
    assert validate_general_answer(
        {"kind": "refuse", "text": "", "reason": "diagnosis", "unmeasured": True}
    ) is None


async def test_the_adapter_appends_the_fixed_sentence_when_the_marker_is_set() -> None:
    """문장은 코드가 붙인다 — 모델 산문이 아니다 (#278)."""
    adapter = GeneralCapabilityAdapter(
        generate=_fake_generate({"kind": "answer", "text": "버스 구간은 걷지 않으셨어요.",
                                 "unmeasured": True})
    )
    result = await adapter.run(_general_request(), request_id="r")
    assert result.status is CapabilityStatus.OK
    assert result.data["answer"].endswith(DISTANCE_FROM_RECORDED_WALKS_ONLY)
    # 본문은 손대지 않는다 — 무손실
    assert result.data["answer"].startswith("버스 구간은 걷지 않으셨어요.")


async def test_no_marker_means_no_sentence() -> None:
    """급여량을 물어본 사람에게 산책 고지가 따라붙으면 안 된다 — 마커를 고른 이유 그 자체."""
    adapter = GeneralCapabilityAdapter(
        generate=_fake_generate({"kind": "answer", "text": "하루 두 번이 보통이에요."})
    )
    result = await adapter.run(_general_request(), request_id="r")
    assert DISTANCE_FROM_RECORDED_WALKS_ONLY not in result.data["answer"]
```

`_fake_generate` 와 `_general_request` 는 `test_orchestration_general_fallback.py` 에 이미 있는 헬퍼를 쓴다. 이름이 다르면 그 파일의 것을 그대로 쓴다.

- [ ] **Step 2: 실패를 확인한다**

```bash
cd backend && uv run pytest tests/test_orchestration_general_fallback.py -k unmeasured -q
```
Expected: FAIL — `extra="forbid"` 라 `unmeasured` 가 들어간 입력이 `None` 이 된다

- [ ] **Step 3: 계약에 칸을 연다**

`GeneralAnswer` 에 (`reason` 위):

```python
    #: 이 아이의 이동량(거리·시간)을 물었는데 기록으로 못 답하는 경우 (D-072).
    #: **모델은 이 칸만 세우고, 사용자에게 나가는 문장은 어댑터가 코드에서 붙인다** —
    #: `axes` 와 같은 규칙이고 이유는 #278 이다. 답변일 때만 쓴다.
    unmeasured: Literal[True] | None = None
```

`shape_matches_kind` 에 규칙을 더한다. **`kind == "ask"` 의 이른 `return self` 보다 앞**이어야 한다 — 안 그러면 되묻기에 붙은 마커가 검사를 빠져나간다:

```python
        if self.unmeasured is not None and self.kind != "answer":
            raise ValueError(f"a {self.kind} carries no unmeasured marker")
```

- [ ] **Step 4: 어댑터가 문장을 붙인다**

`run()` 의 OK 경로를 고친다:

```python
        text = answer.text.strip()
        if answer.unmeasured:
            # 제품 문장이다 — 모델이 쓰지 않는다 (#278). 본문 뒤에 붙이고 본문은 안 고친다.
            text = f"{text}\n\n{DISTANCE_FROM_RECORDED_WALKS_ONLY}"
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={"answer": text},
            elapsed_ms=_elapsed_ms(started),
        )
```

`redirects` import 에 `DISTANCE_FROM_RECORDED_WALKS_ONLY` 를 더한다. 모듈 독스트링에 한 문단을 더해 **마커를 고른 이유와 되돌리는 방법**(`if answer.unmeasured:` → `if payload.walk_activity is None:`)을 적는다.

- [ ] **Step 5: 통과와 전체 회귀를 확인한다**

```bash
cd backend && uv run pytest -q
```
Expected: 전부 통과. **여기서 처음으로 전체 스위트를 돌린다** — `CapabilityName` 을 안 건드렸으므로 오케스트레이션 테스트 전부가 그대로 통과해야 한다.

- [ ] **Step 6: 커밋**

```bash
git add backend/src/daengs_backend/orchestration/adapters/general.py \
        backend/tests/test_orchestration_general_fallback.py \
        backend/tests/test_assistant_walk_activity.py
git commit -m "feat: unmeasured 마커 — 못 재는 이유를 고정 문장으로 붙인다 (D-072)"
```

---

### Task 7: 측정 케이스와 결정 기록

**Files:**
- Modify: `backend/evals/conversation_quality/cases_v1.jsonl`
- Modify: `docs/decisions.md`

**Interfaces:**
- Consumes: Task 6 의 완성된 동작
- Produces: 없음 (산출물은 문서와 케이스)

**⚠️ `cases_v1.jsonl` 은 동결 자산이다.** 2026-09-10 계획서의 Global Constraints 가 파일 sha256 을 랩 헤더에 적게 돼 있다 — **케이스를 더하면 이전 랩과 분모가 달라진다.** 그래서 이 태스크는 기존 줄을 **한 글자도 안 고치고 끝에만 더하며**, 그 사실을 `README.md` 에 적는다.

- [ ] **Step 1: 케이스 3건을 더한다**

`cases_v1.jsonl` 끝에 세 줄을 붙인다. 모양은 기존 줄과 같다 (`case_id` · `turns`[`role`,`text`]).

- `cq_distance_described_route_01` — 스크린샷 그대로: 대중교통이 섞인 경로를 말로 설명하고 거리를 묻는다. **기대: `unmeasured` 가 서고 고지가 나간다.**
- `cq_distance_recorded_01` — 기록이 있는 날 "오늘 얼마나 걸었어?". **기대: 마커가 안 서고 숫자가 나간다.**
- `cq_distance_partially_measured_01` — 3건 중 2건만 측정된 날. **기대: 두 수를 갈라 말한다.**

- [ ] **Step 2: README 에 분모가 바뀐 것을 적는다**

`backend/evals/conversation_quality/README.md` 에 한 절을 더한다: D-072 로 케이스 3건이 늘었고, **이전 랩들과 직접 비교하려면 그 3건을 빼고 봐야 한다**는 것, 그리고 새 sha256.

- [ ] **Step 3: D-072 를 쓴다**

`docs/decisions.md` 끝에 `## D-072` 를 더하고, 맨 위 인덱스 표에도 한 줄 더한다.

제목: **「못 재는 이유를 말한다 — D-051 ⑤ 디스클로저의 조건부 판본」**

본문에 반드시 들어갈 것:
- 무엇을 막았나: 지명 지오코딩·journey 호출로 거리를 추정하는 길을 **안 열었다**. D-051 을 지킨다
- 대신 무엇을 했나: 기록된 산책을 컨텍스트로 싣고, 못 잴 때 **이유를 고정 문장으로** 말한다
- **왜 무조건이 아니라 마커인가**: 이 문서 「설계 근거」 절의 논거를 옮겨 적는다 — Place·vet_contact 는 능력이 곧 범위라 무조건일 수 있고, General 은 catch-all 이라 깎을 범위가 없다. 매번 나오는 줄은 사용자가 건너뛴다
- **이 결정이 지는 빚**: 마커 누락은 사용자가 이유를 못 듣는 것이다. `evals/conversation_quality` 의 케이스 3건이 그 누락률을 잰다. **누락률이 나쁘면 무조건으로 내린다** — `if answer.unmeasured:` 를 `if payload.walk_activity is None:` 으로 바꾸는 한 줄이고, 되돌리기 싼 쪽을 먼저 고른 것이 이 결정의 순서다
- 범위 밖: 분량 하한(`briefly (3 to 5 sentences)`) 수정은 기본 본문을 고치는 일이라 v9 가 되고 D-057 ③ 의 84건 승인 계보가 끊긴다. **별도 카드**다

- [ ] **Step 4: 커밋**

```bash
git add backend/evals/conversation_quality/cases_v1.jsonl \
        backend/evals/conversation_quality/README.md docs/decisions.md
git commit -m "docs: D-072 — 못 재는 이유를 말한다, 그리고 누락을 재는 케이스 셋"
```

---

## 완료 뒤

- [ ] **유료 랩은 사람 승인 뒤에만.** `conversation_quality` 를 실모델로 돌려 마커 누락률을 재는 것은 이 계획서 밖이다. 승인받고 돌린 뒤 결과를 D-072 에 덧붙인다.
- [ ] **배포는 별개다.** 2026-09-12 기준 서버 체크아웃은 `912e0240`(general v3)로 보이고 `origin/dev` 와 733커밋 차이다. 이 카드를 머지해도 그 격차가 그대로면 사용자에게 안 닿는다. 확인: `docker compose exec backend python -c "from daengs_backend.orchestration.adapters.general import GENERAL_PROMPT_VERSION as v; print(v)"`
- [ ] `superpowers:finishing-a-development-branch` 로 마무리한다.
