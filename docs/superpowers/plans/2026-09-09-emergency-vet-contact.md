# 응급 동물병원 연락(`vet_contact`) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 응급으로 판정된 발화에 훈련·산책 답 대신 **가까운 동물병원의 전화번호**를 내주는 능력 하나(`vet_contact`)를 추가한다. 앱은 어느 병원이 잘한다고 말하지 않는다.

**Architecture:** 결정론적 어휘 게이트를 의미 라우터 **앞**에 세워 응급 경로에서 모델 호출을 0회로 만든다. 게이트가 켜지면 `vet_contact` 하나만 배타 실행하고, adapter 는 place-search 의 기존 `/v2/places/search` 에 `kinds=["hospital"]` 로 붙는다(place-search 무변경). 좌표가 없으면 되묻지 않고 `ABSTAINED` + 코드로 끝낸다.

**Tech Stack:** Python 3.12 · FastAPI · Pydantic v2 · httpx · pytest (`uv run pytest`, `backend/` 에서)

**Spec:** `docs/superpowers/specs/2026-09-09-emergency-vet-contact-design.md`

## Global Constraints

- **작업 디렉터리는 `backend/`** 다. 모든 `pytest` 명령은 거기서 돈다. `uv run` 을 거쳐 실행한다 (Python 3.12 고정).
- **응급 경로에 LLM 호출이 있으면 안 된다.** 이 계획의 어떤 테스트도 유료 API 를 부르지 않는다.
- **`daengs_training` 을 import 하지 않는다.** 어휘는 `daengs_backend/orchestration/emergency.py` 가 소유한다 (spec §3).
- **place-search 를 고치지 않는다.** `backend/src/daengs_place/` 아래 파일은 이 계획에서 한 줄도 바뀌지 않는다.
- **어휘에 `이물질` 을 넣지 않는다.** `귓속 이물질`(#350 의 `medical_terms_v1_curated.json`)을 부분일치로 삼킨다. `이물 섭취` 를 쓴다 (spec §3-1-1).
- **매칭은 형태소 분석 없는 평문 부분일치**다. 새 매칭 전략을 만들지 않는다.
- 반경은 **10000m**, `limit_per_kind` 는 **5**.
- 좌표 상자는 조립 계약의 좁은 쪽: `lat` 33.0~39.0 · `lon` 124.0~132.0.

---

### Task 1: 응급 어휘와 게이트

**Files:**
- Create: `backend/src/daengs_backend/orchestration/emergency.py`
- Test: `backend/tests/test_orchestration_emergency_gate.py`

**Interfaces:**
- Consumes: 없음 (leaf)
- Produces: `is_emergency(query: str) -> bool` · `HIGH_TERMS: tuple[str, ...]` · `AMBIGUOUS_TERMS: tuple[str, ...]` · `URGENCY_TERMS: tuple[str, ...]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_orchestration_emergency_gate.py`:

```python
"""응급 게이트 — 결정론적 어휘 판정. 모델 호출 0회, 유료 호출 0회."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from daengs_backend.orchestration.emergency import is_emergency

EVALS = Path(__file__).resolve().parents[1] / "evals"


@pytest.mark.parametrize(
    "query",
    [
        "우리 보리가 갑자기 몸을 떨면서 경련을 일으키고 있어요",
        "콩이가 방금 식탁 위에 있던 초콜릿을 좀 먹었는데 어떡하죠?",
        "강아지가 갑자기 숨을 헐떡이면서 거품을 물고 쓰러졌어요!",
        "산책하다가 바닥에 떨어진 걸 주워 먹었어요",
        "밤새 계속 토해요",
    ],
)
def test_emergency_utterances_fire(query: str) -> None:
    assert is_emergency(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "어제 한 번 토했어요",
        "주워 먹지 말라는 '놔' 신호를 처음부터 어떻게 알려줘야 해요?",
        "요즘 주식 시장 너무 어렵지 않아? 나 이번에 크게 물렸는데",
        "집에서 해줄 수 있는 응급처치가 있을까요?",
        "강아지 귀에 이물질이 들어간 것 같은데 어떻게 빼나요?",
        "근처 동물병원 찾아줘",
    ],
)
def test_non_emergency_utterances_do_not_fire(query: str) -> None:
    assert is_emergency(query) is False
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_orchestration_emergency_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'daengs_backend.orchestration.emergency'`

- [ ] **Step 3: 게이트를 구현한다**

`backend/src/daengs_backend/orchestration/emergency.py`:

```python
"""응급 발화 판정 — 결정론적 어휘 게이트 (설계: docs/superpowers/specs/2026-09-09-emergency-vet-contact-design.md).

**`daengs_training.guardrails.medical` 을 쓰지 않는다.** 그 모듈은 whitelist 가 사전보다
우선이라 "산책 중에 발작을 일으켜요" 를 통과시킨다 — 훈련 게이트에는 맞는 판단이지만
(훈련 질문을 의료로 막지 않으려는 것) 응급 게이트가 물려받으면 응급 문장을 놓친다.
씨앗은 그 저장소의 `data/guardrail/medical_terms_v2.json` 의 `응급·증상` 8개이고,
여기서 두 가지를 바꿨다.

**① 단독으로는 응급이 아닌 말을 뺐다.** 구토·설사·고열·탈수는 `AMBIGUOUS_TERMS` 로
내리고 `URGENCY_TERMS` 와 함께 나올 때만 켠다. "어제 한 번 토했어요" 에 병원 목록이
뜨면 게이트 신뢰가 먼저 무너진다. 이 결합 규칙은 D-064 ② 가 말하는 것과 같은 모양이다.

**② 시제로 좁혔다.** `주워 먹` 은 훈련 질문 "주워 먹지 말라는 '놔' 신호를..." 을 잡는다
(동결 골든셋 `gold_v1.jsonl` 에 실재). 배타 실행이라 훈련 답이 통째로 사라진다.
`주워 먹었`·`주워 먹어` 로 좁혔다 — **이미 일어난 일이 응급이고, 가르치는 법은 훈련이다.**

**`이물질` 을 넣지 말 것.** #350 이 싣는 `medical_terms_v1_curated.json` 의 `귓속 이물질`
을 부분일치로 삼켜, 귀 이물질 질문이 의료 게이트에 도달하지 못한다. `이물 섭취` 를 쓴다.

매칭은 형태소 분석 없는 평문 부분일치다 — 코드베이스에 이미 있는 방식이고, 두 번째
매칭 전략을 만들 측정 근거가 없다. 활용형은 어간으로 끊는다(`토해`·`토하`·`토했`).
"""

from __future__ import annotations

#: 단독으로 응급. 하나라도 걸리면 켠다.
HIGH_TERMS: tuple[str, ...] = (
    "발작", "경련", "호흡곤란", "숨을 못", "숨을 쉬", "숨을 헐떡", "숨을 잘 못",
    "중독", "의식이 없", "실신", "쓰러", "마비", "휘청", "몸을 떨",
    "거품을 물", "혀가 파래",
    "출혈", "피를 토", "토혈", "혈변", "혈뇨", "각혈",
    "교통사고", "차에 치", "삼켰", "이물 섭취", "주워 먹었", "주워먹었", "주워 먹어",
    "열사병", "배가 부풀", "소변을 못", "난산",
    "초콜릿", "양파", "포도", "자일리톨",
    "응급실", "응급 진료", "응급진료", "위급", "다쳤",
    "개한테 물렸", "개에게 물렸", "뱀에 물렸", "벌에 쏘",
)

#: 단독으로는 응급이 아니다. `URGENCY_TERMS` 와 함께일 때만 켠다.
AMBIGUOUS_TERMS: tuple[str, ...] = (
    "구토", "토해", "토하", "토했", "설사", "고열", "탈수", "기력",
)

#: 위급 수식어. 빈도·지속·무력을 나타내는 말이다.
URGENCY_TERMS: tuple[str, ...] = (
    "계속", "자꾸", "멈추지 않", "밤새", "하루 종일",
    "여러 번", "몇 번을", "축 늘어", "못 일어나", "반응이 없",
)


def is_emergency(query: str) -> bool:
    """이 발화가 응급인가. 모델을 부르지 않고 문자열만 본다."""
    if any(term in query for term in HIGH_TERMS):
        return True
    return any(term in query for term in AMBIGUOUS_TERMS) and any(
        term in query for term in URGENCY_TERMS
    )


__all__ = ["AMBIGUOUS_TERMS", "HIGH_TERMS", "URGENCY_TERMS", "is_emergency"]
```

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/test_orchestration_emergency_gate.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: 코퍼스 회귀 테스트를 추가한다**

같은 파일 끝에 붙인다:

```python
def _queries(path: Path) -> list[tuple[str, str]]:
    """(식별자, 질의) 목록. 주석 줄(`#`)과 빈 줄은 건너뛴다."""
    rows: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        item = json.loads(line)
        query = item.get("query") or item.get("question")
        if not query:
            continue
        rows.append((item.get("question_id") or item.get("case_id") or query[:20], query))
    return rows


def test_emergency_recall_on_frozen_answer_quality_corpus() -> None:
    """`emergency__*` stratum 은 전부 켜져야 한다 — 미탐이 오탐보다 훨씬 비싸다."""
    path = EVALS / "answer_quality" / "questions_v1.jsonl"
    missed = [
        item["question_id"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
        for item in [json.loads(line)]
        if item["stratum"].startswith("emergency__") and not is_emergency(item["query"])
    ]
    assert missed == []


def test_false_positive_rate_stays_at_the_measured_single_case() -> None:
    """비응급에서 켜지는 것은 1건뿐이다 (2026-09-09 실측).

    그 1건(`medical_boundary__polite_01`)은 "사료를 거의 안 먹고 구토를 계속하는데 지금
    바로 병원에 가봐야 하는 상황일까요" 다. stratum 은 medical_boundary 지만 실제로 응급에
    가깝고 사용자가 병원 여부를 직접 묻는다 — 켜지는 쪽이 맞다고 보고 남긴다.
    """
    path = EVALS / "answer_quality" / "questions_v1.jsonl"
    fired = sorted(
        item["question_id"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
        for item in [json.loads(line)]
        if not item["stratum"].startswith("emergency__") and is_emergency(item["query"])
    )
    assert fired == ["medical_boundary__polite_01"]


@pytest.mark.parametrize(
    "relative",
    [
        "orchestration_router/gold_v1.jsonl",
        "orchestration_router/gold_place_v1.jsonl",
        "training_quality/questions_v1.jsonl",
        "answer_quality/questions_screening_v2.jsonl",
    ],
)
def test_gate_never_fires_on_frozen_non_emergency_sets(relative: str) -> None:
    """게이트는 배타 실행이라, 여기서 켜지면 그 코퍼스의 정답이 통째로 사라진다.

    동결 라우터 벤치마크를 다시 돌리지 않아도 되는 근거가 이 테스트다 —
    라우터에 도달하는 질의가 하나도 안 바뀐다.
    """
    fired = [ident for ident, query in _queries(EVALS / relative) if is_emergency(query)]
    assert fired == []
```

- [ ] **Step 6: 회귀 테스트가 통과하는지 확인한다**

Run: `uv run pytest tests/test_orchestration_emergency_gate.py -v`
Expected: PASS (17 passed). 실패하면 어휘를 넓히지 말고 **왜 걸렸는지 먼저 본다** — 배타 실행이라 오탐 하나가 그 코퍼스의 답을 지운다.

- [ ] **Step 7: 커밋**

```bash
git add backend/src/daengs_backend/orchestration/emergency.py backend/tests/test_orchestration_emergency_gate.py
git commit -m "feat: 응급 어휘 게이트 — 결정론적 판정, 모델 호출 0회"
```

---

### Task 2: 능력 계약 (`VET_CONTACT` · `VetContactPayload`)

**Files:**
- Modify: `backend/src/daengs_backend/orchestration/contracts.py` (`CapabilityName` 15-24 · `CapabilityPayload` 187 · `_PAYLOAD_TYPES` 188-194)
- Test: `backend/tests/test_orchestration_vet_contact_contract.py`

**Interfaces:**
- Consumes: 없음
- Produces: `CapabilityName.VET_CONTACT` (값 `"vet_contact"`) · `VetContactPayload(lat: float | None, lon: float | None, at_night: bool)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_orchestration_vet_contact_contract.py`:

```python
"""vet_contact 의 payload 계약 — 좌표는 둘 다 있거나 둘 다 없거나."""

from __future__ import annotations

import pytest
from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    VetContactPayload,
)
from pydantic import ValidationError


def test_coordinates_are_optional_together() -> None:
    payload = VetContactPayload(at_night=True)
    assert payload.lat is None
    assert payload.lon is None


def test_half_a_coordinate_is_rejected() -> None:
    with pytest.raises(ValidationError, match="lat and lon must be given together"):
        VetContactPayload(lat=37.5665, at_night=False)


def test_out_of_box_coordinate_is_rejected() -> None:
    with pytest.raises(ValidationError):
        VetContactPayload(lat=10.0, lon=126.978, at_night=False)


def test_request_parses_the_payload_by_capability_name() -> None:
    request = CapabilityRequest.model_validate(
        {
            "capability": "vet_contact",
            "payload": {"lat": 37.5665, "lon": 126.978, "at_night": False},
        }
    )
    assert request.capability is CapabilityName.VET_CONTACT
    assert isinstance(request.payload, VetContactPayload)


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        VetContactPayload(at_night=False, rating=5)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_orchestration_vet_contact_contract.py -v`
Expected: FAIL — `ImportError: cannot import name 'VetContactPayload'`

- [ ] **Step 3: 계약을 넣는다**

`contracts.py` 의 `CapabilityName` 에 `GENERAL` **아래**로 추가:

```python
    #: 응급 발화의 병원 연락 능력. GENERAL 과 마찬가지로 `semantic.ExecuteName` 에 없지만
    #: 이유가 정반대다 — GENERAL 은 모델이 근거 있는 능력과 바꿔치기하지 못하게 뺐고,
    #: 이것은 **모델을 아예 안 태우려고** 뺐다. 결정론적 어휘 게이트와 명시 신호로만 들어온다.
    VET_CONTACT = "vet_contact"
```

`GeneralPayload` 정의 **아래**, `CapabilityPayload` 위에 추가:

```python
class VetContactPayload(ContractModel):
    """응급 병원 연락의 입력. 질의 원문을 싣지 않는다 — 검색어가 아니라 좌표로만 찾는다.

    좌표가 ``None`` 일 수 있는 것이 이 payload 의 요점이다. `vet_contact` 는
    `planner._NEEDS_COORDINATES` 에 들어가지 않으므로 좌표가 없어도 CLARIFY 가 걸리지
    않는다 — 응급에 "위도를 알려주세요" 로 되묻는 것이 최악이기 때문이다. 대신 adapter 가
    ABSTAINED + `vet_contact.location_required` 로 끝낸다.

    반쪽 좌표는 거부한다. 신뢰하지 않는 좌표는 좌표가 아니라는 D-051 ③ 의 처분과 같다.
    """

    model_config = ConfigDict(extra="forbid")

    lat: float | None = Field(None, ge=33.0, le=39.0)
    lon: float | None = Field(None, ge=124.0, le=132.0)
    #: planner 가 채운다. adapter 가 시계를 읽으면 테스트가 시계에 묶인다 —
    #: 이 저장소는 `SearchMust.judge_at`·`evaluated_at` 으로 시각을 인자로 넘긴다.
    at_night: bool

    @model_validator(mode="after")
    def coordinates_come_as_a_pair(self) -> VetContactPayload:
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be given together")
        return self
```

유니온과 매핑을 고친다:

```python
CapabilityPayload = (
    TrainingPayload | LifePayload | WalkPayload | PlacePayload | GeneralPayload | VetContactPayload
)

_PAYLOAD_TYPES = {
    CapabilityName.TRAINING: TrainingPayload,
    CapabilityName.LIFE: LifePayload,
    CapabilityName.WALK: WalkPayload,
    CapabilityName.PLACE: PlacePayload,
    CapabilityName.GENERAL: GeneralPayload,
    CapabilityName.VET_CONTACT: VetContactPayload,
}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/test_orchestration_vet_contact_contract.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 라벨을 넣고 기존 스위트가 안 깨지는지 본다**

`aggregate.py` 의 `_LABELS` 에 추가:

```python
    # 배타 실행이라 단독 결과여서 화면에 안 찍힌다. 그래도 빠뜨리면 KeyError 다 (GENERAL 과 같다).
    CapabilityName.VET_CONTACT: "응급",
```

Run: `uv run pytest tests/test_orchestration_aggregate.py tests/test_orchestration_contracts.py -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/src/daengs_backend/orchestration/contracts.py backend/src/daengs_backend/orchestration/aggregate.py backend/tests/test_orchestration_vet_contact_contract.py
git commit -m "feat: vet_contact 능력 계약 — 좌표는 옵셔널이되 짝으로만"
```

---

### Task 3: 응급 문구

**Files:**
- Modify: `backend/src/daengs_backend/orchestration/redirects.py`
- Test: `backend/tests/test_orchestration_vet_contact_copy.py`

**Interfaces:**
- Consumes: 없음
- Produces: `VET_CONTACT_CALL_FIRST: dict[bool, str]` (키는 `at_night`) · `VET_CONTACT_HOURS_UNKNOWN: str` · `VET_CONTACT_LOCATION_UNKNOWN: str` · `VET_CONTACT_CURRENT_LOCATION_FRAME: str` · 기존 `SCOPED_REDIRECT_MESSAGES["emergency"]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_orchestration_vet_contact_copy.py`:

```python
"""응급 문구는 제품 문장이다 — 모델이 짓지 않고, 경로가 달라도 같은 말이 나간다."""

from __future__ import annotations

from daengs_backend.orchestration.redirects import (
    SCOPED_REDIRECT_MESSAGES,
    VET_CONTACT_CALL_FIRST,
    VET_CONTACT_CURRENT_LOCATION_FRAME,
    VET_CONTACT_HOURS_UNKNOWN,
    VET_CONTACT_LOCATION_UNKNOWN,
)


def test_night_and_day_differ_only_in_what_to_ask_on_the_phone() -> None:
    assert VET_CONTACT_CALL_FIRST[True] == "전화로 야간 진료 여부를 먼저 확인하세요."
    assert VET_CONTACT_CALL_FIRST[False] == "전화로 지금 진료 가능한지 먼저 확인하세요."


def test_hours_unknown_sentence_admits_what_we_do_not_have() -> None:
    assert VET_CONTACT_HOURS_UNKNOWN == (
        "진료 시간과 응급 진료 여부는 공공 데이터에 없어서 확인해 드릴 수 없습니다."
    )


def test_emergency_opener_is_reused_not_rewritten() -> None:
    """같은 상황이 경로에 따라 다른 문장으로 나오면 안 된다."""
    assert SCOPED_REDIRECT_MESSAGES["emergency"] == "응급 상황으로 보여요. 지금 바로 동물병원으로 가세요."


def test_location_unknown_does_not_ask_a_question() -> None:
    """응급에 되묻지 않는다 — 물음표가 있으면 CLARIFY 처럼 읽힌다."""
    assert "?" not in VET_CONTACT_LOCATION_UNKNOWN
    assert VET_CONTACT_LOCATION_UNKNOWN == "현재 위치를 알 수 없어 가까운 병원을 찾지 못했습니다."


def test_location_frame_matches_place_wording() -> None:
    assert VET_CONTACT_CURRENT_LOCATION_FRAME == "현재 기기 위치를 기준으로"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_orchestration_vet_contact_copy.py -v`
Expected: FAIL — `ImportError: cannot import name 'VET_CONTACT_CALL_FIRST'`

- [ ] **Step 3: 문구를 넣는다**

`redirects.py` 의 `SCOPED_REDIRECT_MESSAGES` **아래**에 추가:

```python
# 응급 병원 연락(`vet_contact`)의 문구. 위 리다이렉트와 같은 모듈에 두는 이유도 같다 —
# 같은 상황이 경로에 따라 다른 문장으로 나오면 사용자가 그것을 다른 판정으로 읽는다.
# 첫 줄은 새로 짓지 않고 `SCOPED_REDIRECT_MESSAGES["emergency"]` 를 그대로 쓴다.

#: 전화 우선. 키는 `VetContactPayload.at_night` 이다.
#: 야간 순위 부스트는 이 카드에 없다 — `24h` 태그 개수를 아직 재지 못했고, 그때까지
#: 시간대는 **무엇을 물어볼지**만 바꾼다 (설계 §2-3).
VET_CONTACT_CALL_FIRST: dict[bool, str] = {
    True: "전화로 야간 진료 여부를 먼저 확인하세요.",
    False: "전화로 지금 진료 가능한지 먼저 확인하세요.",
}

#: **조건 없이 나간다.** 후보가 있든 없든 항상 참이고, 가끔만 나오는 고지는 사용자가
#: 기댈 수 없다 (D-051 ⑤ 가 위치 고지에 내린 것과 같은 판단). 이 한 줄이 이 기능의
#: 정직성 전부다 — 인허가 원천에는 진료시간도 응급 여부도 없다.
VET_CONTACT_HOURS_UNKNOWN = (
    "진료 시간과 응급 진료 여부는 공공 데이터에 없어서 확인해 드릴 수 없습니다."
)

#: 좌표가 없을 때. **물음표를 넣지 말 것** — 되묻는 문장으로 읽히면 CLARIFY 를 피한 의미가 없다.
VET_CONTACT_LOCATION_UNKNOWN = "현재 위치를 알 수 없어 가까운 병원을 찾지 못했습니다."

#: Place 의 같은 고지와 **같은 문자열**이다. 두 능력이 위치를 다르게 부르면 안 된다.
VET_CONTACT_CURRENT_LOCATION_FRAME = "현재 기기 위치를 기준으로"
```

`__all__` 을 갱신한다:

```python
__all__ = [
    "NO_CAPABILITY_MESSAGE",
    "SCOPED_REDIRECT_MESSAGES",
    "VET_CONTACT_CALL_FIRST",
    "VET_CONTACT_CURRENT_LOCATION_FRAME",
    "VET_CONTACT_HOURS_UNKNOWN",
    "VET_CONTACT_LOCATION_UNKNOWN",
    "RefusalReason",
]
```

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/test_orchestration_vet_contact_copy.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/src/daengs_backend/orchestration/redirects.py backend/tests/test_orchestration_vet_contact_copy.py
git commit -m "feat: 응급 병원 연락 문구 — 모르는 것을 무조건 밝히는 한 줄 포함"
```

---

### Task 4: adapter — `/v2/places/search` 호출과 상태 매핑

**Files:**
- Create: `backend/src/daengs_backend/orchestration/adapters/vet_contact.py`
- Modify: `backend/src/daengs_backend/orchestration/adapters/__init__.py`
- Test: `backend/tests/test_orchestration_vet_contact_adapter.py`

**Interfaces:**
- Consumes: Task 2 의 `CapabilityName.VET_CONTACT`·`VetContactPayload`, Task 3 의 문구 상수
- Produces: `VetContactCapabilityAdapter(client: httpx.AsyncClient | None = None, base_url: str | None = None, timeout_ms: int | None = None)` — `.run(request, *, request_id) -> CapabilityResult`. `data` 는 `{"answer": str, "searched_radius_m": int, "at_night": bool, "candidates": list[dict], "notices": list[dict]}`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_orchestration_vet_contact_adapter.py`:

```python
"""응급 병원 연락의 HTTP 경계와 CapabilityResult 매핑."""

from __future__ import annotations

import json

import httpx
from daengs_backend.orchestration.adapters.vet_contact import VetContactCapabilityAdapter
from daengs_backend.orchestration.contracts import (
    CapabilityRequest,
    CapabilityStatus,
    VetContactPayload,
)


def _request(*, lat: float | None = 37.5665, lon: float | None = 126.978, night: bool = False):
    return CapabilityRequest(
        capability="vet_contact",
        payload=VetContactPayload(lat=lat, lon=lon, at_night=night),
    )


def _search_payload(count: int = 2) -> dict:
    return {
        "groups": [
            {
                "kind": "hospital",
                "limit": 5,
                "truncated": False,
                "results": [
                    {
                        "place": {
                            "key": {"source": "public:mois:animal_hospital", "ref": f"r{i}"},
                            "name": f"{i}번 동물병원",
                            "lat": 37.5,
                            "lng": 127.0,
                            "distance_m": 100 * (i + 1),
                            "match": {"kind": "hospital"},
                            "classifications": [{"source": {"source": "s", "ref": "r"}}],
                            "facts": {
                                "address": f"서울시 어딘가 {i}",
                                "phone": f"02-000-000{i}",
                                "medical": {
                                    "active": True,
                                    "license_status_name": "영업/정상",
                                    "open_now": None,
                                },
                            },
                        },
                        "evaluations": {},
                    }
                    for i in range(count)
                ],
            }
        ]
    }


async def test_adapter_asks_only_for_hospitals_around_the_trusted_point() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_search_payload())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(
            client=client, base_url="http://place-search:8000"
        ).run(_request(), request_id="request-1")
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.OK
    [sent] = seen
    assert sent.url == "http://place-search:8000/v2/places/search"
    assert sent.headers["X-Request-ID"] == "request-1"
    assert json.loads(sent.content) == {
        "lat": 37.5665,
        "lng": 126.978,
        "radius_m": 10000,
        "kinds": ["hospital"],
        "limit_per_kind": 5,
    }


async def test_candidates_carry_phone_distance_and_nothing_that_ranks_quality() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_search_payload(count=1))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(client=client).run(
            _request(), request_id="request-2"
        )
    finally:
        await client.aclose()

    [candidate] = result.data["candidates"]
    assert candidate == {
        "name": "0번 동물병원",
        "phone": "02-000-0000",
        "distance_m": 100,
        "address": "서울시 어딘가 0",
        "license_status_name": "영업/정상",
        "open_now": None,
    }


async def test_missing_coordinates_abstain_without_calling_place_search() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("좌표가 없으면 HTTP 를 부르지 않는다")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(client=client).run(
            _request(lat=None, lon=None), request_id="request-3"
        )
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.ABSTAINED
    assert result.abstention.code == "vet_contact.location_required"
    assert "응급 상황으로 보여요" in result.abstention.message
    assert "현재 위치를 알 수 없어" in result.abstention.message


async def test_zero_candidates_is_ok_not_an_error() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"groups": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(client=client).run(
            _request(), request_id="request-4"
        )
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.OK
    assert result.data["candidates"] == []


async def test_hours_unknown_notice_is_present_whether_or_not_there_are_candidates() -> None:
    async def empty(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"groups": []})

    async def full(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_search_payload())

    for handler in (empty, full):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await VetContactCapabilityAdapter(client=client).run(
                _request(), request_id="request-5"
            )
        finally:
            await client.aclose()
        codes = [notice["code"] for notice in result.data["notices"]]
        assert "vet_contact.hours_unknown" in codes
        assert "place.searched_around_current_location" in codes


async def test_night_changes_only_what_to_ask_on_the_phone() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_search_payload())

    answers = {}
    for night in (True, False):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await VetContactCapabilityAdapter(client=client).run(
                _request(night=night), request_id="request-6"
            )
        finally:
            await client.aclose()
        answers[night] = result.data["answer"]

    assert "야간 진료 여부를" in answers[True]
    assert "지금 진료 가능한지" in answers[False]
    for answer in answers.values():
        assert answer.startswith("응급 상황으로 보여요. 지금 바로 동물병원으로 가세요.")
        assert "확인해 드릴 수 없습니다" in answer


async def test_upstream_failure_is_an_error_result_not_an_exception() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(client=client).run(
            _request(), request_id="request-7"
        )
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.ERROR
    assert result.error.kind == "vet_contact_upstream_failure"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_orchestration_vet_contact_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: ...adapters.vet_contact`

- [ ] **Step 3: adapter 를 구현한다**

`backend/src/daengs_backend/orchestration/adapters/vet_contact.py`:

```python
"""응급 병원 연락의 HTTP 경계.

**place-search 에 새 endpoint 를 만들지 않는다.** `/v2/places/search` 는 `kinds=["hospital"]`
을 받으면 이미 `resolve_medical_places`(MOIS 권위 원천만)로 가고 `facts.phone` 을 실어 준다.
LLM 을 하나도 거치지 않으므로 응급 경로에 모델 호출이 0회다 — Place discovery 를 쓰지 않는
이유가 그것이다(그쪽은 intent proposer 를 태운다).

**반경이 Place discovery 의 3km 와 다르다.** 응급에 "반경 안에 없습니다" 는 답이 되면 안 되고,
결과는 어차피 거리순이라 넓혀도 가까운 것부터 나온다. 지방에서 3km 는 0곳이 흔하다. 대신
후보마다 `distance_m` 을 실어, 25km 짜리를 "가까운 병원" 으로 읽지 않게 한다.

**`data` 에 평점·리뷰·추천 이유 필드를 두지 않는다.** 그런 필드가 없는 것이 이 계약의 목적이다.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from daengs_backend.config import settings
from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    ErrorDetail,
    OutcomeDetail,
    VetContactPayload,
)
from daengs_backend.orchestration.redirects import (
    SCOPED_REDIRECT_MESSAGES,
    VET_CONTACT_CALL_FIRST,
    VET_CONTACT_CURRENT_LOCATION_FRAME,
    VET_CONTACT_HOURS_UNKNOWN,
    VET_CONTACT_LOCATION_UNKNOWN,
)

_SEARCH_PATH = "/v2/places/search"
_RADIUS_M = 10_000
_LIMIT_PER_KIND = 5
_EMERGENCY_OPENER = SCOPED_REDIRECT_MESSAGES["emergency"]

_HOURS_UNKNOWN_NOTICE = {
    "code": "vet_contact.hours_unknown",
    "message": VET_CONTACT_HOURS_UNKNOWN,
}
_LOCATION_FRAME_NOTICE = {
    "code": "place.searched_around_current_location",
    "message": f"{VET_CONTACT_CURRENT_LOCATION_FRAME} 가까운 순으로 찾았습니다.",
}


class VetContactCapabilityAdapter:
    capability = CapabilityName.VET_CONTACT

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        self._client = client
        self._base_url = (
            settings.place_search_base_url if base_url is None else base_url
        ).rstrip("/")
        timeout = settings.place_discovery_timeout_ms if timeout_ms is None else timeout_ms
        if timeout <= 0:
            raise ValueError("vet contact timeout must be positive")
        self._timeout_s = timeout / 1_000

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        started = time.perf_counter()
        payload = request.payload
        if not isinstance(payload, VetContactPayload):
            return self._error(started, "invalid_payload", "응급 병원 요청이 올바르지 않습니다.")

        if payload.lat is None or payload.lon is None:
            # 되묻지 않는다. 클라이언트는 문장이 아니라 이 code 를 보고 위치 설정 CTA 를 단다.
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.ABSTAINED,
                abstention=OutcomeDetail(
                    code="vet_contact.location_required",
                    message=f"{_EMERGENCY_OPENER}\n{VET_CONTACT_LOCATION_UNKNOWN}",
                ),
                elapsed_ms=_elapsed_ms(started),
            )

        body = {
            "lat": payload.lat,
            "lng": payload.lon,
            "radius_m": _RADIUS_M,
            "kinds": ["hospital"],
            "limit_per_kind": _LIMIT_PER_KIND,
        }
        try:
            response = await self._post(body, request_id=request_id)
        except httpx.TimeoutException:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.TIMEOUT,
                error=ErrorDetail(
                    kind="vet_contact_timeout",
                    detail="병원 목록 응답 시간이 초과됐습니다.",
                ),
                elapsed_ms=_elapsed_ms(started),
            )
        except httpx.RequestError:
            return self._error(
                started, "vet_contact_unavailable", "병원 목록 기능에 연결할 수 없습니다."
            )

        if not response.is_success:
            kind = (
                "vet_contact_upstream_failure"
                if response.status_code >= 500
                else "vet_contact_invalid_request"
            )
            return self._error(started, kind, "병원 목록을 가져오지 못했습니다.")

        try:
            candidates = _candidates(response.json())
        except (ValueError, TypeError, KeyError):
            return self._error(
                started, "vet_contact_invalid_response", "병원 목록을 해석할 수 없습니다."
            )

        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={
                "answer": _answer(candidates, at_night=payload.at_night),
                "searched_radius_m": _RADIUS_M,
                "at_night": payload.at_night,
                "candidates": candidates,
                "notices": [dict(_HOURS_UNKNOWN_NOTICE), dict(_LOCATION_FRAME_NOTICE)],
            },
            elapsed_ms=_elapsed_ms(started),
        )

    async def _post(self, body: dict[str, Any], *, request_id: str) -> httpx.Response:
        url = f"{self._base_url}{_SEARCH_PATH}"
        kwargs = {
            "json": body,
            "headers": {"X-Request-ID": request_id},
            "timeout": self._timeout_s,
        }
        if self._client is not None:
            return await self._client.post(url, **kwargs)
        async with httpx.AsyncClient() as client:
            return await client.post(url, **kwargs)

    def _error(self, started: float, kind: str, detail: str) -> CapabilityResult:
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.ERROR,
            error=ErrorDetail(kind=kind, detail=detail),
            elapsed_ms=_elapsed_ms(started),
        )


def _candidates(payload: Any) -> list[dict[str, Any]]:
    """v2 검색 응답에서 연락에 필요한 것만 꺼낸다.

    `open_now` 는 **싣되 지금은 전량 None** 이다. MOIS 적재가 `place.hours` 를 NULL 로 쓰기
    때문인데(`ingest/mois_store.py`), 계약에 자리를 두면 진료시간 원천이 생겼을 때 계약
    변경 없이 채워진다. 모르는 것을 없는 것으로 바꾸지 않는다.
    """
    if not isinstance(payload, dict):
        raise TypeError("search response must be an object")
    out: list[dict[str, Any]] = []
    for group in payload.get("groups") or []:
        for hit in group.get("results") or []:
            place = hit["place"]
            facts = place.get("facts") or {}
            medical = facts.get("medical") or {}
            out.append(
                {
                    "name": place["name"],
                    "phone": facts.get("phone"),
                    "distance_m": place["distance_m"],
                    "address": facts.get("address"),
                    "license_status_name": medical.get("license_status_name"),
                    "open_now": medical.get("open_now"),
                }
            )
    return out


def _answer(candidates: list[dict[str, Any]], *, at_night: bool) -> str:
    """네 줄. 첫 줄은 `redirects` 의 것이고 셋째 줄은 조건 없이 나간다."""
    lines = [_EMERGENCY_OPENER, VET_CONTACT_CALL_FIRST[at_night], VET_CONTACT_HOURS_UNKNOWN]
    if candidates:
        lines.append(
            f"{VET_CONTACT_CURRENT_LOCATION_FRAME} 가까운 순으로 {len(candidates)}곳을 찾았습니다."
        )
    else:
        lines.append(
            f"{VET_CONTACT_CURRENT_LOCATION_FRAME} 찾아봤지만 "
            f"반경 {_RADIUS_M // 1000}km 안에서는 등록된 동물병원을 찾지 못했습니다."
        )
    return "\n".join(lines)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1_000)


__all__ = ["VetContactCapabilityAdapter"]
```

`adapters/__init__.py` 에 추가:

```python
from daengs_backend.orchestration.adapters.vet_contact import VetContactCapabilityAdapter
```

그리고 `__all__` 에 `"VetContactCapabilityAdapter"` 를 넣는다.

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/test_orchestration_vet_contact_adapter.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/src/daengs_backend/orchestration/adapters/vet_contact.py backend/src/daengs_backend/orchestration/adapters/__init__.py backend/tests/test_orchestration_vet_contact_adapter.py
git commit -m "feat: vet_contact adapter — 기존 v2 병원 검색에 붙고 LLM 을 안 태운다"
```

---

### Task 5: planner — 게이트와 명시 신호를 같은 계획으로

**Files:**
- Modify: `backend/src/daengs_backend/orchestration/planner.py` (모듈 docstring · `_EXECUTION_ORDER` 60 · `resolve_deterministic_route` 83-110 · `_payload_for` 195-240)
- Modify: `backend/src/daengs_backend/orchestration/service.py:124` 근처
- Test: `backend/tests/test_orchestration_vet_contact_routing.py`

**Interfaces:**
- Consumes: Task 1 의 `is_emergency`, Task 2 의 계약
- Produces: `resolve_emergency_route(*, query: str, context: dict[str, Any], requested_capability: str | None, at_night: bool) -> RoutePlan | None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_orchestration_vet_contact_routing.py`:

```python
"""응급 경로의 계획 — 배타 실행, 되묻지 않기, 두 진입점의 동치성."""

from __future__ import annotations

from daengs_backend.orchestration.contracts import CapabilityName, RouterKind
from daengs_backend.orchestration.planner import resolve_emergency_route

SEOUL = {"location": {"lat": 37.5665, "lon": 126.978}}


def test_lexicon_gate_produces_a_single_vet_contact_request() -> None:
    plan = resolve_emergency_route(
        query="우리 보리가 갑자기 경련을 일으켜요",
        context=SEOUL,
        requested_capability=None,
        at_night=False,
    )
    assert plan is not None
    assert [request.capability for request in plan.requests] == [CapabilityName.VET_CONTACT]
    assert plan.clarify is None
    assert plan.handoffs == []
    assert plan.router is RouterKind.DETERMINISTIC
    assert plan.model is None


def test_explicit_signal_builds_the_identical_plan() -> None:
    """두 진입점이 다른 계획을 내면 사용자가 같은 상황에서 다른 답을 받는다."""
    by_lexicon = resolve_emergency_route(
        query="우리 보리가 갑자기 경련을 일으켜요",
        context=SEOUL,
        requested_capability=None,
        at_night=True,
    )
    by_signal = resolve_emergency_route(
        query="우리 보리가 갑자기 경련을 일으켜요",
        context=SEOUL,
        requested_capability="vet_contact",
        at_night=True,
    )
    assert by_lexicon == by_signal


def test_explicit_signal_works_even_when_the_lexicon_does_not_fire() -> None:
    plan = resolve_emergency_route(
        query="병원 좀",
        context=SEOUL,
        requested_capability="vet_contact",
        at_night=False,
    )
    assert plan is not None
    assert [request.capability for request in plan.requests] == [CapabilityName.VET_CONTACT]


def test_missing_coordinates_still_execute_and_never_clarify() -> None:
    plan = resolve_emergency_route(
        query="강아지가 숨을 잘 못 쉬어요",
        context={},
        requested_capability=None,
        at_night=False,
    )
    assert plan is not None
    assert plan.clarify is None
    [request] = plan.requests
    assert request.payload.lat is None
    assert request.payload.lon is None


def test_out_of_box_coordinates_are_treated_as_absent() -> None:
    """신뢰하지 않는 좌표는 좌표가 아니다 (D-051 ③)."""
    plan = resolve_emergency_route(
        query="강아지가 숨을 잘 못 쉬어요",
        context={"location": {"lat": 10.0, "lon": 126.978}},
        requested_capability=None,
        at_night=False,
    )
    assert plan is not None
    [request] = plan.requests
    assert request.payload.lat is None


def test_at_night_reaches_the_payload() -> None:
    plan = resolve_emergency_route(
        query="강아지가 경련을 일으켜요",
        context=SEOUL,
        requested_capability=None,
        at_night=True,
    )
    [request] = plan.requests
    assert request.payload.at_night is True


def test_vet_contact_is_not_a_shared_assembler_destination() -> None:
    """공용 조립기가 이 능력의 payload 를 만들면 안 된다 (D-051 ②).

    `_EXECUTE_NAMES` 에 이름이 새면 명시 신호가 `resolve_deterministic_route` 로 흘러
    `_payload_for` 의 raise 에 걸린다. 지금 안 터지는 것은 순서 덕분이라, 계약으로 잰다.
    """
    from daengs_backend.orchestration.planner import _EXECUTE_NAMES

    assert "vet_contact" not in _EXECUTE_NAMES


def test_non_emergency_returns_none_so_normal_routing_continues() -> None:
    assert (
        resolve_emergency_route(
            query="근처 동물병원 찾아줘",
            context=SEOUL,
            requested_capability=None,
            at_night=False,
        )
        is None
    )
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_orchestration_vet_contact_routing.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_emergency_route'`

- [ ] **Step 3: planner 에 응급 분기를 넣는다**

`planner.py` 의 import 에 추가:

```python
from daengs_backend.orchestration.emergency import is_emergency
```

`_EXECUTION_ORDER` 를 고친다 (`vet_contact` 는 배타라 순서가 실제로 쓰이진 않지만, 이름이 빠져 있으면 `_EXECUTION_INDEX` 조회가 조용히 꼴찌로 밀린다):

```python
_VET_CONTACT = "vet_contact"
_EXECUTION_ORDER = ("training", "life", "walk", "place", _GENERAL, _VET_CONTACT)
```

**`_EXECUTE_NAMES` 에서 반드시 빼야 한다.** 지금 정의는 `_GENERAL` 하나만 제외하므로,
`_EXECUTION_ORDER` 에 이름을 더하는 순간 `vet_contact` 가 `_EXECUTE_NAMES` 에 들어간다.
그러면 `requested_capability="vet_contact"` 가 `resolve_deterministic_route` →
`assemble_route_plan` → `_payload_for` 로 새어 아래 Step 3 의 `raise` 에 걸린다.
지금은 `resolve_emergency_route` 가 먼저 잡아 주어 안 터지지만, 그건 순서에 기댄 것이라
계약으로 세워 둔다:

```python
# `general` 과 `vet_contact` 는 둘 다 `_EXECUTE_NAMES` 밖이지만 이유가 정반대다.
# general 은 명시 신호로도 못 부르고, vet_contact 는 **명시 신호로만** 부른다 —
# 그 신호는 `resolve_emergency_route` 가 라우터보다 앞에서 소비한다.
_EXECUTE_NAMES = frozenset(
    name for name in _EXECUTION_ORDER if name not in {_GENERAL, _VET_CONTACT}
)
```

`resolve_deterministic_route` **위**에 새 함수를 넣는다:

```python
def resolve_emergency_route(
    *,
    query: str,
    context: dict[str, Any],
    requested_capability: str | None,
    at_night: bool,
) -> RoutePlan | None:
    """응급이면 `vet_contact` 하나짜리 계획을, 아니면 None 을 낸다.

    **의미 라우터보다 앞에 선다.** 그래서 응급 경로에는 모델 호출이 0회다.

    **배타다.** 응급 답에 산책 조건이나 훈련 요령이 섞이면 보호자의 인지 부하만 늘린다.

    **좌표가 없어도 CLARIFY 를 내지 않는다.** `vet_contact` 는 `_NEEDS_COORDINATES` 에
    없고, 좌표는 있으면 싣고 없으면 None 으로 간다 — 응급에 "위도를 알려주세요" 로
    되묻는 것이 최악이기 때문이다. 없는 좌표의 처리는 adapter 가 ABSTAINED 로 한다.

    두 진입점(어휘 게이트 · 명시 신호)이 **이 함수 하나**를 지난다. 계획이 한 곳에서
    만들어져야 두 경로가 서로 다른 답을 낼 수 없다 (D-051 ② 와 같은 이유).
    """
    if requested_capability != _VET_CONTACT and not is_emergency(query):
        return None

    location = _trusted_location(context)
    payload: dict[str, Any] = {"at_night": at_night}
    if location is not None:
        payload["lat"] = location["lat"]
        payload["lon"] = location["lon"]

    return RoutePlan.model_validate(
        {
            "requests": [
                {"capability": _VET_CONTACT, "payload": payload, "timeout_ms": None}
            ],
            "handoffs": [],
            "clarify": None,
            "router": RouterKind.DETERMINISTIC,
            "model": None,
            "prompt_version": None,
        }
    )


def _trusted_location(context: dict[str, Any]) -> dict[str, float] | None:
    """검증된 좌표만 돌려준다. 상자를 벗어나면 **없는 것으로 친다** (D-051 ③).

    `_missing_coordinates` 와 같은 판정을 쓰되, 여기서는 없다고 해서 요청을 세우지 않는다.
    """
    if _missing_coordinates(context):
        return None
    location = context["location"]
    return {"lat": location["lat"], "lon": location["lon"]}
```

`_payload_for` 의 `place` 분기 **아래**, `raise` **위**에 추가:

```python
    if capability == _VET_CONTACT:
        # 이 경로로는 오지 않는다 — `resolve_emergency_route` 가 payload 를 직접 만든다.
        # 그래도 규칙을 적어 두는 이유는 D-051 ② 다: 새 ExecuteName 은 자기 payload 를
        # 적거나 요청을 소리 나게 세우거나 둘 중 하나다.
        raise ValueError(
            "vet_contact payloads are built by resolve_emergency_route, not by the shared assembler"
        )
```

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/test_orchestration_vet_contact_routing.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: service 가 게이트를 먼저 부르게 한다**

`service.py` 의 import 에 추가:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from daengs_backend.orchestration.planner import resolve_emergency_route
```

모듈 상단에 상수를 둔다:

```python
_KST = ZoneInfo("Asia/Seoul")
#: 야간의 경계. 문구만 가르고 순위는 안 바꾸므로 정밀할 필요가 없다 —
#: 야간 순위 부스트는 `24h` 태그 실측 뒤의 별도 카드다.
_NIGHT_FROM_HOUR = 20
_NIGHT_UNTIL_HOUR = 8


def _is_night(now: datetime) -> bool:
    hour = now.astimezone(_KST).hour
    return hour >= _NIGHT_FROM_HOUR or hour < _NIGHT_UNTIL_HOUR
```

`_plan_and_execute` 안에서 `resolve_deterministic_route` 호출 **바로 위**에 넣는다:

```python
        # 응급은 라우터보다 앞이다 — 모델을 태우지 않고, 배타로 끝낸다.
        route_plan = resolve_emergency_route(
            query=query,
            context=structured_context,
            requested_capability=requested_capability,
            at_night=_is_night(datetime.now(tz=_KST)),
        )
        if route_plan is None:
            route_plan = resolve_deterministic_route(
                requested_capability=requested_capability,
                query=query,
                context=structured_context,
            )
```

- [ ] **Step 6: 기존 오케스트레이션 스위트가 안 깨지는지 본다**

Run: `uv run pytest tests/test_orchestration_place_routing.py tests/test_router_benchmark_place_gold.py tests/test_orchestration_general_fallback.py -q`
Expected: PASS. 여기서 깨지면 게이트가 기존 질의를 삼킨 것이므로 **어휘를 다시 본다** (Task 1 의 회귀 테스트가 먼저 잡아야 정상이다).

- [ ] **Step 7: 커밋**

```bash
git add backend/src/daengs_backend/orchestration/planner.py backend/src/daengs_backend/orchestration/service.py backend/tests/test_orchestration_vet_contact_routing.py
git commit -m "feat: 응급 계획을 의미 라우터 앞에 — 배타 실행, 좌표 없어도 안 되묻는다"
```

---

### Task 6: 엔진 등록과 종단 배선

**Files:**
- Modify: `backend/src/daengs_backend/orchestration/graph.py:50-61`
- Test: `backend/tests/test_orchestration_vet_contact_e2e.py`

**Interfaces:**
- Consumes: Task 4 의 `VetContactCapabilityAdapter`, Task 5 의 `resolve_emergency_route`
- Produces: 없음 (배선)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_orchestration_vet_contact_e2e.py`:

```python
"""응급 발화가 엔진을 지나 사용자 문장까지 오는 길."""

from __future__ import annotations

import httpx
from daengs_backend.orchestration.adapters.vet_contact import VetContactCapabilityAdapter
from daengs_backend.orchestration.contracts import (
    AssistantStatus,
    CapabilityName,
    PrincipalContext,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.planner import resolve_emergency_route

SEOUL = {"location": {"lat": 37.5665, "lon": 126.978}}
PRINCIPAL = PrincipalContext(subject="test-user", kind="APP_USER")


def test_engine_registers_the_capability_by_default() -> None:
    engine = OrchestrationEngine()
    assert CapabilityName.VET_CONTACT in engine._adapters


async def test_emergency_utterance_answers_with_phone_numbers() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "groups": [
                    {
                        "kind": "hospital",
                        "limit": 5,
                        "truncated": False,
                        "results": [
                            {
                                "place": {
                                    "key": {"source": "public:mois:animal_hospital", "ref": "1"},
                                    "name": "가까운동물병원",
                                    "lat": 37.5,
                                    "lng": 127.0,
                                    "distance_m": 320,
                                    "match": {"kind": "hospital"},
                                    "classifications": [{"source": {"source": "s", "ref": "r"}}],
                                    "facts": {
                                        "address": "서울시 중구",
                                        "phone": "02-123-4567",
                                        "medical": {"active": True, "open_now": None},
                                    },
                                },
                                "evaluations": {},
                            }
                        ],
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        plan = resolve_emergency_route(
            query="강아지가 초콜릿을 먹었어요",
            context=SEOUL,
            requested_capability=None,
            at_night=False,
        )
        engine = OrchestrationEngine(
            adapters={CapabilityName.VET_CONTACT: VetContactCapabilityAdapter(client=client)}
        )
        response = await engine.run(
            route_plan=plan,
            query="강아지가 초콜릿을 먹었어요",
            principal=PRINCIPAL,
            request_id="request-e2e",
            context=SEOUL,
        )
    finally:
        await client.aclose()

    assert response.status is AssistantStatus.ANSWERED
    assert response.message.startswith("응급 상황으로 보여요.")
    assert "확인해 드릴 수 없습니다" in response.message
    [result] = response.results
    assert result.data["candidates"][0]["phone"] == "02-123-4567"


async def test_missing_location_is_uncertain_and_carries_the_cta_code() -> None:
    plan = resolve_emergency_route(
        query="강아지가 초콜릿을 먹었어요",
        context={},
        requested_capability=None,
        at_night=False,
    )
    engine = OrchestrationEngine(
        adapters={CapabilityName.VET_CONTACT: VetContactCapabilityAdapter()}
    )
    response = await engine.run(
        route_plan=plan,
        query="강아지가 초콜릿을 먹었어요",
        principal=PRINCIPAL,
        request_id="request-nolocation",
    )
    assert response.status is AssistantStatus.UNCERTAIN
    assert response.results[0].abstention.code == "vet_contact.location_required"
    assert "위도" not in response.message
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_orchestration_vet_contact_e2e.py -v`
Expected: FAIL — `assert CapabilityName.VET_CONTACT in engine._adapters` 에서 KeyError/AssertionError

> **`engine.graph.ainvoke` 를 직접 부르지 말 것.** `OrchestratorState` 는 `principal`·`query`·`locale`·`context`·`response`·`include_route_trace` 까지 요구하는 TypedDict 라, 일부만 넣으면 노드에서 KeyError 가 난다. `OrchestrationEngine.run(route_plan=..., query=..., principal=..., request_id=..., context=...)` 가 그 상태를 만들어 주고 `AssistantResponse` 를 바로 돌려준다 — `tests/test_orchestration_graph.py:97` 의 헬퍼가 그 형식이다.

- [ ] **Step 3: 엔진에 등록한다**

`graph.py` 의 import 에 `VetContactCapabilityAdapter` 를 더하고 기본 어댑터 맵에 추가한다:

```python
                CapabilityName.GENERAL: GeneralCapabilityAdapter(),
                # 라우터가 고를 수 없는 능력이다 — `resolve_emergency_route` 만 계획에 넣는다.
                CapabilityName.VET_CONTACT: VetContactCapabilityAdapter(),
```

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/test_orchestration_vet_contact_e2e.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 전체 스위트를 돌린다**

Run: `uv run pytest -q`
Expected: PASS. `ml`·`gait`·`screening` 그룹은 안 깔려 skip 된다 (CI 와 같다).

- [ ] **Step 6: 커밋**

```bash
git add backend/src/daengs_backend/orchestration/graph.py backend/tests/test_orchestration_vet_contact_e2e.py
git commit -m "feat: vet_contact 를 엔진에 등록하고 종단으로 확인"
```

---

### Task 7: fixture 바이트 대조와 짝 대조군

**Files:**
- Create: `backend/tests/fixtures/vet_contact/with_candidates.json`
- Create: `backend/tests/fixtures/vet_contact/location_required.json`
- Create: `backend/tests/test_vet_contact_fixtures.py`
- Create: `backend/evals/orchestration_emergency/gold_pairs_v1.jsonl`
- Modify: `backend/tests/test_orchestration_emergency_gate.py`

**Interfaces:**
- Consumes: Task 4·5 의 산출 전부
- Produces: 없음 (계약 고정)

- [ ] **Step 1: 짝 대조군 파일을 만든다**

D-064 ② 가 결합 규칙에 대해 *"같은 의료 명사를 배경으로 쓰는 정상 훈련 질문을 짝 대조군으로 함께 고정한다"* 를 요구한다. `AMBIGUOUS_TERMS` 넷 각각에 짝을 만든다.

`backend/evals/orchestration_emergency/gold_pairs_v1.jsonl`:

```jsonl
{"pair_id":"vomit","term":"구토","emergency":"밤새 계속 토해요 어떡하죠","normal":"사료 바꾸면 구토하는 애들이 있다던데 천천히 바꾸는 방법이 있나요?"}
{"pair_id":"diarrhea","term":"설사","emergency":"어제부터 설사를 멈추지 않아요","normal":"간식을 많이 주면 설사한다고 해서 하루 몇 개가 적당한지 궁금해요"}
{"pair_id":"fever","term":"고열","emergency":"고열이 하루 종일 안 떨어져요","normal":"여름철 고열 대비해서 산책 시간을 언제로 잡는 게 좋을까요?"}
{"pair_id":"dehydration","term":"탈수","emergency":"축 늘어져 있고 탈수가 온 것 같아요","normal":"산책할 때 탈수 안 되게 물은 얼마나 자주 주면 되나요?"}
```

- [ ] **Step 2: 짝 대조군 테스트를 쓴다**

`tests/test_orchestration_emergency_gate.py` 끝에 추가:

```python
def test_ambiguous_terms_have_paired_controls_that_do_not_fire() -> None:
    """D-064 ② — 결합 규칙을 추가할 때는 정상 질문 짝을 함께 고정한다.

    `AMBIGUOUS_TERMS` 는 단독으로 켜지면 안 되고, 위급 수식어와 함께일 때만 켜져야 한다.
    짝의 `normal` 은 같은 낱말을 **배경으로만** 쓴다.
    """
    path = EVALS / "orchestration_emergency" / "gold_pairs_v1.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows, "짝 대조군이 비어 있으면 결합 규칙에 회귀 방지가 없다"
    for row in rows:
        assert is_emergency(row["emergency"]) is True, row["pair_id"]
        assert is_emergency(row["normal"]) is False, row["pair_id"]


def test_every_ambiguous_term_is_covered_by_a_pair() -> None:
    from daengs_backend.orchestration.emergency import AMBIGUOUS_TERMS

    path = EVALS / "orchestration_emergency" / "gold_pairs_v1.jsonl"
    covered = {
        json.loads(line)["term"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    # 활용형(토해·토하·토했)은 대표형 `구토` 하나로 덮는다.
    representative = {"구토", "설사", "고열", "탈수"}
    assert representative <= covered
    assert representative <= set(AMBIGUOUS_TERMS)
```

- [ ] **Step 3: 짝 대조군 테스트를 돌린다**

Run: `uv run pytest tests/test_orchestration_emergency_gate.py -v`
Expected: PASS. 실패하면 **짝의 `normal` 문장을 고치지 말고** 어휘를 본다 — 정상 문장이 켜진다면 그게 오탐이다.

- [ ] **Step 4: fixture 생성 테스트를 쓴다**

`backend/tests/test_vet_contact_fixtures.py`:

```python
"""Android 가 받는 JSON 을 바이트로 고정한다.

계약이 움직이면 다른 저장소가 아니라 여기서 깨져야 한다 —
`tests/fixtures/place_capability/` 가 같은 이유로 있다 (D-051).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from daengs_backend.orchestration.adapters.vet_contact import VetContactCapabilityAdapter
from daengs_backend.orchestration.contracts import CapabilityRequest, VetContactPayload

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vet_contact"


def _upstream() -> dict:
    return {
        "groups": [
            {
                "kind": "hospital",
                "limit": 5,
                "truncated": False,
                "results": [
                    {
                        "place": {
                            "key": {"source": "public:mois:animal_hospital", "ref": "1"},
                            "name": "가까운동물병원",
                            "lat": 37.5,
                            "lng": 127.0,
                            "distance_m": 320,
                            "match": {"kind": "hospital"},
                            "classifications": [{"source": {"source": "s", "ref": "r"}}],
                            "facts": {
                                "address": "서울시 중구",
                                "phone": "02-123-4567",
                                "medical": {
                                    "active": True,
                                    "license_status_name": "영업/정상",
                                    "open_now": None,
                                },
                            },
                        },
                        "evaluations": {},
                    }
                ],
            }
        ]
    }


async def _result(*, lat: float | None, lon: float | None) -> dict:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_upstream())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await VetContactCapabilityAdapter(client=client).run(
            CapabilityRequest(
                capability="vet_contact",
                payload=VetContactPayload(lat=lat, lon=lon, at_night=False),
            ),
            request_id="fixture",
        )
    finally:
        await client.aclose()
    return json.loads(result.model_dump_json(exclude={"elapsed_ms"}))


async def test_with_candidates_fixture_matches() -> None:
    actual = await _result(lat=37.5665, lon=126.978)
    expected = json.loads((FIXTURES / "with_candidates.json").read_text(encoding="utf-8"))
    assert actual == expected


async def test_location_required_fixture_matches() -> None:
    actual = await _result(lat=None, lon=None)
    expected = json.loads((FIXTURES / "location_required.json").read_text(encoding="utf-8"))
    assert actual == expected


async def test_no_quality_ranking_field_ever_reaches_the_client() -> None:
    """평점·후기·추천 이유가 계약에 들어올 자리가 없어야 한다 (설계 §1-1)."""
    blob = json.dumps(await _result(lat=37.5665, lon=126.978), ensure_ascii=False)
    for forbidden in ("rating", "review", "score", "평점", "후기", "추천 이유"):
        assert forbidden not in blob
```

- [ ] **Step 5: fixture 를 만든다**

테스트를 한 번 돌려 실패시키고(`FileNotFoundError`), 실제 산출을 파일로 적는다:

```bash
uv run pytest tests/test_vet_contact_fixtures.py -v
mkdir -p tests/fixtures/vet_contact
uv run python - <<'PY'
import asyncio, json, pathlib, sys
sys.path.insert(0, "tests")
from test_vet_contact_fixtures import _result, FIXTURES
FIXTURES.mkdir(parents=True, exist_ok=True)
for name, kwargs in [
    ("with_candidates", {"lat": 37.5665, "lon": 126.978}),
    ("location_required", {"lat": None, "lon": None}),
]:
    data = asyncio.run(_result(**kwargs))
    (FIXTURES / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("wrote", name)
PY
```

**쓰기 전에 두 파일을 눈으로 읽는다.** fixture 는 계약이라 잘못된 값을 굳히면 그것이 정답이 된다. `answer` 네 줄이 설계 §4-6 과 같은지, `candidates` 에 평점류 필드가 없는지 확인한다.

- [ ] **Step 6: 통과를 확인한다**

Run: `uv run pytest tests/test_vet_contact_fixtures.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: 전체 스위트와 lint**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: PASS

- [ ] **Step 8: 커밋**

```bash
git add backend/tests/fixtures/vet_contact backend/tests/test_vet_contact_fixtures.py backend/evals/orchestration_emergency backend/tests/test_orchestration_emergency_gate.py
git commit -m "test: vet_contact 계약 fixture 와 AMBIG 짝 대조군 고정 (D-064 ②)"
```

---

### Task 8: 결정 카드

**Files:**
- Modify: `docs/decisions.md`

**Interfaces:**
- Consumes: 없음
- Produces: 없음

- [ ] **Step 1: 번호를 잡는다**

```bash
git fetch origin
git log --all --oneline --grep="예약" | head
grep -oE "D-0[0-9]{2}" docs/decisions.md | sort -u | tail -3
```

`dev` 의 최신 번호 다음을 쓰되, **#350 이 `D-064` 를 이미 잡고 있으므로 그 뒤**를 쓴다. 열린 브랜치까지 확인한다 — `dev` 의 표만 보면 남의 예약을 못 본다.

- [ ] **Step 2: 카드를 쓴다**

맨 위 목차 표에 한 줄을 더하고, 본문에 절을 만든다. 제목은
**"응급에는 실력이 아니라 도달을 답한다 — 후기·평판을 원천으로 쓰지 않는다"**.

본문에 반드시 들어갈 것 (설계 문서 §1 에서 옮긴다):

- #64 가 과목 축을 지운 판정과 같은 구조라는 것 — **신뢰도가 낮은 것과 존재하지 않는 것은 다른 처분을 받는다.** 없는 제도는 데이터를 모아도 생기지 않는다.
- 후기는 응대·주차·대기시간을 재지 수술 성적을 재지 않고, 어려운 케이스를 받는 병원이 나쁜 후기를 받는 **역상관 위험**이 있다.
- 2026-09-09 원천 조사 결과 — 행안부·animal.go.kr·서울시가 같은 지자체 인허가 하나이고, 정부 시스템조차 24시 병원을 상호명 검색(`searchCoNm=24`)으로 대신한다.
- 그래서 축을 **도달**로 바꾸고, 사실이 없는 자리는 **전화**로 넘긴다.
- 되돌리려는 사람이 읽을 조건: 진료시간·응급 수용을 담은 **상시 공개 원천**이 생기면 다시 연다. 후기 크롤은 그 조건이 아니다.

설계 전문은 `docs/superpowers/specs/2026-09-09-emergency-vet-contact-design.md` 를 가리키고, 이 카드에 복사하지 않는다.

- [ ] **Step 3: 커밋**

```bash
git add docs/decisions.md
git commit -m "docs: 응급에는 도달을 답한다 — 후기·평판을 원천으로 쓰지 않는 결정"
```

- [ ] **Step 4: PR 본문을 갱신한다**

```bash
gh pr view 363 --json body -q .body > /tmp/pr363.md
# 작업 목록 체크박스를 채우고 `## 남은 것` 을 실제로 남은 것으로 고친 뒤
gh pr edit 363 --body-file /tmp/pr363.md
```

---

## 이 계획이 하지 않는 것

- **야간 순위 부스트** — `preference_tags(night=, emergency=)` 배선은 place-search 계약 변경이고, 값어치가 `24h` 태그 개수에 달려 있는데 아직 재지 못했다. 시간대는 **문구만** 가른다.
- **place-search 변경** — `backend/src/daengs_place/` 아래는 한 줄도 안 바뀐다.
- **Android CTA 버튼** — 이 저장소의 몫은 `vet_contact.location_required` 가 계약에 있고 fixture 에 고정되는 것까지다.
- **골든셋 쏠림 보정** — 응급 21건 중 절반이 중독이다. 교통사고·난산·열사병·요도폐색·위염전·저혈당은 어휘에만 있고 검증되지 않았다. 별도 카드다.
