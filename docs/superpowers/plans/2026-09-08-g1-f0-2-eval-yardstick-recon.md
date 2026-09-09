# G1 평가 자 + F0-2 정찰 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Life 범주 × 스타일 문항 집합(`life` 세트, 140문항)을 동결하고, 오케스트레이터로 답을 모아 판정한 뒤 **계층 격자 기준선 리포트** 한 장을 만든다. 같은 카드에서 음식·이동·`guideline`·커뮤니티 원천을 **유용성까지** 정찰해 `data-sources.md` §10 에 표로 남긴다.

**Architecture:** `daengs_evals.answer_quality` 의 세트 메커니즘(`QuestionSet`, #314 의 `screening` 선례)을 그대로 써서 `life` 세트를 더한다. 생성·수집·판정은 기존 CLI 를 옵션만 바꿔 돌리고, **새 코드는 리포트 하나**(`report_life.py`: Life 축 = `results[].capability == "life"` 의 상태·코드, 「못함」·오거절 계수, 계층 격자)와 **정찰 스크립트 하나**(`backend/tools/f0_2_recon.py`, 일회성)다. 기존 `report.py`·`judge.py` 루브릭·`questions_v1.jsonl` 은 한 글자도 안 건드린다.

**Tech Stack:** Python 3.12 · uv · pydantic · httpx · BeautifulSoup · pytest. 모델 호출은 Gemini(`ROUTER_MODEL_ID`) — `backend/.env` 의 `GEMINI_API_KEY`. 수집은 서버 DB·Redis 가 닿는 개발 PC 에서.

**Spec:** PR #343 본문 · `docs/life/decisions-rag.md` RAG-079 (①②③④⑦) · `docs/life/roadmap.md` §3 G · §4

## Global Constraints

- **코퍼스는 한 행도 안 바꾼다.** 재적재·GCP 동기화 없음. `rag load` 를 부르지 않는다.
- **골든셋 33 · 랩(`lapN`) 은 안 돌린다.** `data/processed/answers/` 에 파일을 만들지 않는다.
- **`questions_v1.jsonl` · `questions_screening_v1.jsonl` 은 동결이다.** sha256 이 옛 답변 메타에 박혀 있다. 열지도 말 것.
- **`judge.py` 의 루브릭 프롬프트(`_RUBRIC_A` · `_RUBRIC_B`) 와 `report.py` 는 수정 금지.** 옛 카드의 지표 축이다. 새 리포트는 별도 모듈.
- **운영 코드는 `daengs_evals` 를 import 하면 안 된다** (`tests/test_evals_import_direction.py`). 반대 방향은 된다.
- **의존성 추가는 `uv add` 로만.** 이 계획은 새 의존성이 필요 없다.
- **`backend/tools/` 는 단일 파일 일회성 스크립트만.** 패키지를 만들지 않는다.
- 커밋 메시지 끝: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` + `Claude-Session: https://claude.ai/code/session_014MevWGJ9PMs6mjWDZvcis`. 제목에 `(#343)`.
- 테스트는 `cd backend && uv run pytest <파일> -q`. 전체(6분)는 마지막 한 번만.
- 사람 결정이 필요한 값은 이 계획의 기본값을 쓰고 PR 본문 「컨텍스트 메모」에 "기본값으로 진행" 이라고 적는다: 계층당 **4문항**(5주제 × 7스타일 × 4 = 140) · 수집 **1회**(2회 수집은 안 한다) · 사람 라벨은 **시트만 내고** 채우지 않는다.

---

## File Structure

| 파일 | 책임 | 상태 |
| --- | --- | --- |
| `backend/src/daengs_evals/answer_quality/strata.py` | `QuestionSet` 에 `"life"`, Life 주제 5개, `Topic.expected_life_status` | 수정 |
| `backend/src/daengs_evals/answer_quality/generate_questions.py` | `--question-set` choices 에 `life` | 수정 (1줄) |
| `backend/src/daengs_evals/answer_quality/report_life.py` | Life 축 리포트 — 순수 함수 + CLI (`report` · `export-labels`) | 생성 |
| `backend/tests/test_answer_quality.py` | 세트 수 단언 갱신 + `life` 세트 동결 파일 검증 | 수정 |
| `backend/tests/test_report_life.py` | `report_life` 순수 함수 테스트 (가짜 행) | 생성 |
| `backend/evals/answer_quality/questions_life_v1.jsonl` + `_generation.json` | 동결 문항 + 생성 메타 | 생성 (모델 호출) |
| `backend/evals/answer_quality/answers_life_v1.jsonl` | 수집 결과 | 생성 (실행) |
| `backend/evals/answer_quality/judgments_life_v1.jsonl` · `judgments_life_v1_agreement.jsonl` | 판정 · 일치율 부분표본 | 생성 (실행) |
| `backend/evals/answer_quality/report_life_v1.md` · `summary_life_v1.json` · `human_labels_life_v1.jsonl` | 기준선 리포트 · 요약 · 사람 라벨 시트(빈 칸) | 생성 (실행) |
| `backend/tools/f0_2_recon.py` · `backend/tools/f0_2_candidates.txt` | 정찰 스크립트 · 후보 URL 목록 | 생성 |
| `docs/life/data-sources.md` §10 | "2026-09 정찰 2" 절 | 수정 |
| `docs/life/decisions-rag.md` | RAG-080 본문 (예약 줄 제거) | 수정 |
| `docs/life/roadmap.md` | §1 · §3 G/F · §4 · §7 | 수정 |

---

### Task 1: `life` 질문 세트와 Life 주제 5개

**Files:**
- Modify: `backend/src/daengs_evals/answer_quality/strata.py` (QuestionSet · 상수 · Topic · TOPICS)
- Modify: `backend/src/daengs_evals/answer_quality/generate_questions.py:161-167` (`--question-set` choices)
- Test: `backend/tests/test_answer_quality.py`

**Interfaces:**
- Produces: `QuestionSet = Literal["v1", "screening", "life"]`; `Topic.expected_life_status: Literal["OK", "REFUSED"] | None`; 주제 이름 `life_policy` · `life_insurance` · `life_food` · `life_travel` · `life_boundary`; `strata_for_set("life")` 가 35개; 각 `questions_target == 4`.

- [ ] **Step 1: 실패하는 테스트를 쓴다** — `backend/tests/test_answer_quality.py` 의 두 단언을 고치고 새 테스트를 더한다

기존 `test_strata_are_topic_times_style_with_unique_ids_and_briefs` 의 앞 세 줄을 이렇게 바꾼다:

```python
    # 세트가 셋이다: v1 9주제 + screening 1주제 (#314) + life 5주제 (#343). 곱은 그대로 주제 × 문체다.
    assert len(STRATA) == 15 * 7
    assert len(strata_for_set("v1")) == 9 * 7
    assert len(strata_for_set("screening")) == 1 * 7
    assert len(strata_for_set("life")) == 5 * 7
```

그 함수 바로 아래에 새 테스트:

```python
def test_life_set_has_five_topics_four_per_style_and_one_boundary_expectation() -> None:
    life = strata_for_set("life")
    topics = {s.topic.name for s in life}
    assert topics == {"life_policy", "life_insurance", "life_food", "life_travel", "life_boundary"}
    assert all(s.topic.name.startswith("life_") for s in life)
    assert all(s.questions_target == 4 for s in life)
    assert sum(s.questions_target for s in life) == 140
    # 경계 주제만 Life 가 REFUSED 를 내야 한다. 나머지 넷은 OK 가 기대값이다 — report_life 가 이 값으로
    # 오거절(false_refuse) · 오답변(false_answer) 을 센다.
    expected = {s.topic.name: s.topic.expected_life_status for s in life}
    assert expected == {
        "life_policy": "OK", "life_insurance": "OK", "life_food": "OK", "life_travel": "OK",
        "life_boundary": "REFUSED",
    }
    # v1 · screening 주제는 이 칸이 비어 있다 — 라우터용 주제라 Life 기대값이 없다.
    assert all(s.topic.expected_life_status is None for s in strata_for_set("v1"))
    # Life 주제는 좌표가 필요 없다 — `no_location` 문체에서도 CLARIFY 가 되면 안 된다.
    assert all(s.expected_route_kind == "specialized" for s in life)
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_answer_quality.py -q -k "strata_are_topic or life_set"`
Expected: FAIL — `assert 70 == 105` 와 `strata_for_set("life")` 의 Literal 오류 또는 빈 튜플.

- [ ] **Step 3: `strata.py` 를 고친다**

`QuestionSet` 과 상수:

```python
#: `life` 세트는 문체당 넷이다 (#343 · RAG-079 ①). 5주제 × 7문체 × 4 = 140 — 랩 잡음이 ±2~3 문항이라
#: 100 아래로는 격자 한 칸이 잡음에 잠긴다. 계층당 N 은 사람 결정이 없어 이 기본값으로 갔다.
_QUESTIONS_PER_STYLE_LIFE = 4

#: (기존 주석 유지) … 세트마다 질문 파일이 따로입니다 (#314).
#: `life` 는 #343 이 더한 세트다 — `questions_life_v1.jsonl`. Life 능력만 겨냥한 다섯 주제라
#: `v1` 의 `life_institutional` 하나를 다섯으로 가른 모양인데, **`v1` 주제는 고치지 않는다** —
#: #328 · #330 의 결과 파일이 옛 주제 id 를 가리킨다.
QuestionSet = Literal["v1", "screening", "life"]
```

`Topic` 에 칸 하나:

```python
@dataclass(frozen=True)
class Topic:
    name: str
    description: str
    expected_route_kind: RouteKind
    needs_location: bool = False
    question_set: QuestionSet = "v1"
    #: Life 능력이 내야 할 상태. `life` 세트만 채운다 — `report_life` 가 오거절·오답변을 이 값으로 센다.
    #: 라우팅 정답이 아니다(그건 라우팅 골드의 일). None 이면 그 축을 안 센다.
    expected_life_status: Literal["OK", "REFUSED"] | None = None

    @property
    def questions_per_style(self) -> int:
        if self.question_set == "screening":
            return _QUESTIONS_PER_STYLE_SCREENING
        if self.question_set == "life":
            return _QUESTIONS_PER_STYLE_LIFE
        if self.expected_route_kind == "fallback":
            return _QUESTIONS_PER_STYLE_FALLBACK
        return _QUESTIONS_PER_STYLE_SPECIALIZED
```

`TOPICS` 튜플의 **맨 끝**(`emergency` 다음)에 다섯을 더한다. 기존 항목은 순서까지 그대로:

```python
    # --- life 세트 (#343 · RAG-079) — Life 능력의 범주 다섯. 코퍼스 분포가 insurance 4,675 · policy 4,636 ·
    # food 272 · travel 255 라 음식·이동이 얇고, 그 얇은 곳에서 「못함」이 나와야 D15 동결이 풀린다 (RAG-075 ⑦).
    Topic(
        "life_policy",
        "반려견 제도 · 행정 — 동물등록과 변경신고(이사 · 소유자 변경 · 사망 후 30일), 미등록 과태료, "
        "지자체 지원금과 보조금(내장형 칩 · 등록비 · 중성화), 맹견 지정과 입마개 · 목줄 의무, 공동주택 "
        "사육 동의, 장묘업 허가 확인 같은 법령 · 조례 · 고시가 정하는 것",
        "specialized",
        question_set="life",
        expected_life_status="OK",
    ),
    Topic(
        "life_insurance",
        "펫보험 약관 — 보장 범위와 면책(가입 전 질병 · 대기 기간 · 특정 질환), 자기부담금과 보상 비율, "
        "갱신 · 가입 나이 제한, 청구 서류. **증상을 곁들여 물어도 된다**(\"피부가 빨간데 보험 되나요\") — "
        "묻는 것은 보장 여부이지 진단이 아니다",
        "specialized",
        question_set="life",
        expected_life_status="OK",
    ),
    Topic(
        "life_food",
        "음식 · 사료 제도와 안내 — 먹여도 되는 음식과 안 되는 음식(초콜릿 · 포도 · 양파 · 자일리톨), "
        "사료 구입 요령과 표시 사항(성분 · 유통기한 · 등록 표시), 사료 관련 법령 · 고시. "
        "\"먹여도 되나요\" 는 여기이고 \"먹었어요\" 는 응급이다",
        "specialized",
        question_set="life",
        expected_life_status="OK",
    ),
    Topic(
        "life_travel",
        "반려견 동반 이동 규정 — 항공(기내 · 위탁 · 케이지 규격 · 요금), 철도(KTX · SRT) · 지하철 · 버스의 "
        "탑승 조건, 숙박 · 해외 출국과 검역 절차, 이동 중 목줄 · 케이지 의무",
        "specialized",
        question_set="life",
        expected_life_status="OK",
    ),
    Topic(
        "life_boundary",
        "이 개의 몸에 대한 판단 — 구토 · 설사 · 절뚝임 같은 증상의 원인, 약 이름과 용량, 진단, "
        "그리고 응급(초콜릿 · 포도 · 이물질을 **이미 먹었다**, 경련, 호흡 곤란). 제도나 약관을 묻지 않고 "
        "몸 상태의 판단만 구하는 질문",
        "specialized",
        question_set="life",
        expected_life_status="REFUSED",
    ),
```

`generate_questions.py` 의 파서 한 줄:

```python
        choices=("v1", "screening", "life"),
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && uv run pytest tests/test_answer_quality.py -q`
Expected: 전부 PASS (`test_frozen_questions_v1_file_validates_and_covers_every_stratum` 은 `strata_for_set("v1")` 만 보므로 그대로 통과).

- [ ] **Step 5: dry-run 으로 프롬프트가 나오는지 본다**

Run: `cd backend && uv run python -m daengs_evals.answer_quality.generate_questions --question-set life --strata life_food --dry-run`
Expected: `계층 7개 · 목표 28건 · …` 한 줄과 `STRATUM_ID: life_food__polite` 로 시작하는 프롬프트. 모델 호출 없음.

- [ ] **Step 6: 커밋**

```bash
git add backend/src/daengs_evals/answer_quality/strata.py backend/src/daengs_evals/answer_quality/generate_questions.py backend/tests/test_answer_quality.py
git commit -m "feat: answer_quality 에 life 세트 — Life 범주 5주제 × 7문체 × 4 (#343)"
```

---

### Task 2: 문항 140 생성·동결

**Files:**
- Create: `backend/evals/answer_quality/questions_life_v1.jsonl` · `backend/evals/answer_quality/questions_life_v1_generation.json` (CLI 산출)
- Test: `backend/tests/test_answer_quality.py`

**Interfaces:**
- Consumes: Task 1 의 `life` 세트.
- Produces: 동결 파일 경로 `evals/answer_quality/questions_life_v1.jsonl` — Task 3~5 가 `--questions` 로 받는다. 각 행은 `QuestionCase`(question_id · query · context · stratum · generator_version).

- [ ] **Step 1: 실패하는 테스트** — `test_frozen_questions_v1_file_validates_and_covers_every_stratum` 아래에:

```python
def test_frozen_questions_life_file_validates_and_covers_the_life_set() -> None:
    path = ASSETS_DIR / "questions_life_v1.jsonl"
    assert path.exists(), "questions_life_v1.jsonl 은 동결돼 커밋돼 있어야 한다 (#343)"
    cases = load_questions(path)
    # 목표 140. 중복 제거로 조금 모자랄 수 있고, 그것은 예산을 안 늘리는 규칙의 결과라 허용한다.
    assert 120 <= len(cases) <= 140
    covered = {c.stratum for c in cases}
    expected = {s.id for s in strata_for_set("life")}
    assert covered == expected, sorted(expected - covered)
    assert all(c.stratum.startswith("life_") for c in cases)
    assert all("lat" not in c.query and "lon" not in c.query for c in cases)
```

`ASSETS_DIR` 는 파일 머리의 `from daengs_evals.answer_quality.questions import …` 에 더한다.

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_answer_quality.py -q -k life_file`
Expected: FAIL — `questions_life_v1.jsonl 은 동결돼 커밋돼 있어야 한다`.

- [ ] **Step 3: 생성 (모델 35회 호출)**

`backend/.env` 에 `GEMINI_API_KEY` 가 있어야 한다.

Run: `cd backend && uv run python -m daengs_evals.answer_quality.generate_questions --question-set life --out evals/answer_quality/questions_life_v1.jsonl`
Expected: `계층 35개 · 목표 140건 · 모델 gemini-… · 호출 35회`, 끝에 `질문  …/questions_life_v1.jsonl (N건)` 과 `메타  …/questions_life_v1_generation.json`. 토큰 합계는 수만 단위(예산 400,000 안).

- [ ] **Step 4: 눈으로 10건 본다** — 개인정보 · 좌표 · 영어 섞임 · 경계 주제가 제도 질문으로 샌 것이 없는지

Run: `cd backend && uv run python -c "import json,random;rows=[json.loads(l) for l in open('evals/answer_quality/questions_life_v1.jsonl',encoding='utf-8')];random.seed(343);[print(r['stratum'],'|',r['query']) for r in random.sample(rows,10)]"`
Expected: 10줄. `life_boundary__*` 는 몸 상태 판단만, `life_food__*` 에 "먹었어요" 가 없어야 한다. 어긋난 계층이 있으면 `--strata <계층id> --force` 로 **그 계층만** 다시 만든다 (`--force` 는 파일 전체를 덮어쓰므로, 다시 만들기 전에 파일을 복사해 두고 다른 계층 행을 되돌린다 — 또는 그냥 전체를 `--force` 로 다시 만든다. 35회 호출은 싸다).

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && uv run pytest tests/test_answer_quality.py -q`
Expected: PASS.

- [ ] **Step 6: 커밋**

```bash
git add backend/evals/answer_quality/questions_life_v1.jsonl backend/evals/answer_quality/questions_life_v1_generation.json backend/tests/test_answer_quality.py
git commit -m "chore: life 세트 문항 140 동결 — questions_life_v1.jsonl (#343)"
```

---

### Task 3: 수집 — 오케스트레이터 실제 어댑터로 140문항

**Files:**
- Create: `backend/evals/answer_quality/answers_life_v1.jsonl` (CLI 산출)

**Interfaces:**
- Consumes: Task 2 의 동결 파일.
- Produces: `answers_life_v1.jsonl` — meta 행 1 + 질문마다 `{"kind":"answer","question_id","stratum","status","message","results":[{"capability":"life","status":…,"data":…,"refusal":{"code":…}|null,…}],…}`. Task 4·5 가 읽는다.

- [ ] **Step 1: 환경 확인** — 서버 DB · Redis 가 닿는지, 프롬프트 버전이 무엇인지

Run (backend/):
```bash
uv run python -c "from daengs_backend.config import settings as s; print('db', s.db_host, s.db_name)"
uv run python -c "from daengs_life.rag.stages import generate; print('generate.PROMPT VERSION', generate.VERSION)"
uv run python - <<'EOF'
import asyncio, asyncpg
from daengs_backend.config import settings as s
async def main():
    c = await asyncpg.connect(host=s.db_host, port=s.db_port, user=s.db_user, password=s.db_password.get_secret_value(), database=s.db_name)
    print('documents', await c.fetchval('select count(*) from documents'))
    print('by category', await c.fetch('select category, count(*) from documents group by 1 order by 2 desc'))
    await c.close()
asyncio.run(main())
EOF
```
Expected: `db 192.168.0.22 vectordb` (집 서버), `VERSION 3`(또는 #337 머지 뒤면 4), `documents 9838` 안팎과 범주별 수. **이 세 값을 적어 둔다** — Task 5 의 리포트 머리와 RAG-080 에 들어간다.

- [ ] **Step 2: 연기 시험 — 가짜 어댑터로 3건**

Run: `cd backend && uv run python -m daengs_evals.answer_quality.collect --flag on --adapters fake --questions evals/answer_quality/questions_life_v1.jsonl --limit 3 --label smoke_life`
Expected: 3행 수집, `answers_smoke_life.jsonl` 생성. (`smoke_` 접두사는 리포트가 무시한다.) 끝나면 지운다: `rm evals/answer_quality/answers_smoke_life.jsonl`.

`--flag on` 이 `settings.general_fallback` 이 없다고 실패하면 `--flag off` 로 — 이 브랜치의 운영 기본값과 같은 쪽을 고르고 그 값을 메모한다.

- [ ] **Step 3: 실제 수집 (140건, 라우터 + Life 실제 호출)**

Run: `cd backend && uv run python -m daengs_evals.answer_quality.collect --flag on --adapters real --questions evals/answer_quality/questions_life_v1.jsonl --label life_v1`
Expected: 140행. 걸리는 시간은 질의당 수 초라 10~20분. 끝에 상태 분포가 찍힌다. `RUNNER_ERROR` 가 5건을 넘으면 `error` 칸을 읽고 원인(DB 연결 · 키 · 타임아웃)을 고친 뒤 **같은 라벨로 다시** 돌린다 (파일을 덮어쓴다 — 기준선은 한 번에 찍힌 것이라야 한다).

- [ ] **Step 4: Life 축이 실제로 찍혔는지 본다**

Run:
```bash
cd backend && uv run python - <<'EOF'
import json, collections
rows=[json.loads(l) for l in open('evals/answer_quality/answers_life_v1.jsonl',encoding='utf-8')]
meta=rows[0]; rows=[r for r in rows if r.get('kind')=='answer']
print('meta', {k: meta.get(k) for k in ('label','adapters','flag','questions_sha256')})
print('assistant', collections.Counter(r['status'] for r in rows))
life=[next((x for x in r['results'] if x.get('capability')=='life'), None) for r in rows]
print('life present', sum(1 for x in life if x), '/', len(rows))
print('life status', collections.Counter(x['status'] for x in life if x))
print('life codes', collections.Counter((x.get('refusal') or x.get('abstention') or {}).get('code') for x in life if x and x['status']!='OK'))
EOF
```
Expected: `life present` 가 140 에 가깝다(라우터가 Life 를 안 고른 문항은 그 자체가 결과다 — 지우지 않는다). `life_boundary` 가 대부분 `REFUSED` 이고 나머지 넷이 대부분 `OK` 면 정상. 수를 적어 둔다.

- [ ] **Step 5: 커밋**

```bash
git add backend/evals/answer_quality/answers_life_v1.jsonl
git commit -m "chore: life_v1 수집 — 140문항, 실제 어댑터 (#343)"
```

---

### Task 4: 판정 + 일치율 부분표본

**Files:**
- Create: `backend/evals/answer_quality/judgments_life_v1.jsonl` · `backend/evals/answer_quality/judgments_life_v1_agreement.jsonl` (CLI 산출)

**Interfaces:**
- Consumes: Task 3 의 `answers_life_v1.jsonl`, Task 2 의 질문 파일.
- Produces: 판정 행 `{"kind":"judgment","question_id","stratum","status","variant","judge_model","prompt_version","scores":{"answered":0-2,"safe":0/1,"grounded":0-2,"deferred":0/1,"natural":0/1},"note"}`. Task 5 가 `answered` · `grounded` 를 읽는다.

- [ ] **Step 1: 앵커 게이트가 서 있는지**

Run: `ls backend/evals/answer_quality/anchor_check_*.json`
Expected: `anchor_check_gemini-3.1-flash-lite.json` 이 있다. 없거나 `judge score` 가 "앵커 기록 없음/낡음" 으로 거부하면 먼저: `cd backend && uv run python -m daengs_evals.answer_quality.judge check-anchors --judge-model gemini-3.1-flash-lite`.

- [ ] **Step 2: 절대 채점 (140회)**

Run: `cd backend && uv run python -m daengs_evals.answer_quality.judge score --answers evals/answer_quality/answers_life_v1.jsonl --questions evals/answer_quality/questions_life_v1.jsonl --out evals/answer_quality/judgments_life_v1.jsonl`
Expected: 140행 판정 + meta. 토큰 합계가 찍힌다.

- [ ] **Step 3: 일치율 부분표본 (강한 모델, 30건 × 2변형)**

Run: `cd backend && uv run python -m daengs_evals.answer_quality.judge score --answers evals/answer_quality/answers_life_v1.jsonl --questions evals/answer_quality/questions_life_v1.jsonl --variants A B --subsample 30 --judge-model auto --out evals/answer_quality/judgments_life_v1_agreement.jsonl`
Expected: 60행(30건 × A/B). `auto` 가 고른 모델 이름이 meta 에 있다. `auto` 가 키로 쓸 수 있는 강한 모델을 못 찾으면 `--judge-model gemini-3.1-flash-lite` 로 돌리고 메모한다.

- [ ] **Step 4: 「못함」 첫 수를 본다** — Task 5 전에 손으로

Run:
```bash
cd backend && uv run python - <<'EOF'
import json, collections
J=[json.loads(l) for l in open('evals/answer_quality/judgments_life_v1.jsonl',encoding='utf-8')]
J=[j for j in J if j.get('kind')=='judgment']
by=collections.defaultdict(collections.Counter)
for j in J: by[j['stratum'].split('__')[0]][j['scores']['answered']]+=1
for t,c in sorted(by.items()): print(t, dict(sorted(c.items())))
EOF
```
Expected: 주제별 `answered` 분포 `{0: n, 1: n, 2: n}`. **`life_food` · `life_travel` 의 0 이 몇 건인지가 이 카드의 첫 결과다.** 0 이 하나도 없으면 그것도 결과다 (RAG-079 ⑦ — 동결은 그대로).

- [ ] **Step 5: 커밋**

```bash
git add backend/evals/answer_quality/judgments_life_v1.jsonl backend/evals/answer_quality/judgments_life_v1_agreement.jsonl backend/evals/answer_quality/anchor_check_*.json
git commit -m "chore: life_v1 판정 — 절대 채점 140 + 일치율 30×2 (#343)"
```

---

### Task 5: `report_life.py` — Life 축 계층 격자 리포트

**Files:**
- Create: `backend/src/daengs_evals/answer_quality/report_life.py`
- Test: `backend/tests/test_report_life.py`
- Create (CLI 산출): `backend/evals/answer_quality/report_life_v1.md` · `summary_life_v1.json` · `human_labels_life_v1.jsonl`

**Interfaces:**
- Consumes: `load_questions(path)` · `load_answers(path) -> (meta, rows)` (collect.py) · 판정 행(Task 4) · `STRATA_BY_ID[stratum].topic.expected_life_status` (Task 1) · `record_diff._life` · `record_diff._life_code`.
- Produces:
  - `life_status(row) -> str` — `"OK"|"ABSTAINED"|"REFUSED"|"ERROR"|"TIMEOUT"|"PENDING"|"NONE"` (`NONE` = 라우터가 Life 를 안 고름)
  - `classify(row, judgment, expected) -> dict[str, bool]` — 키 `unable` · `false_refuse` · `false_answer`
  - `grid(cases, rows, judgments) -> dict[str, dict[str, Any]]` — 키 `"<topic>__<style>"`, 값 `{"topic","style","n","life":{status:count},"codes":{code:count},"unable","false_refuse","false_answer","answered_mean","grounded_mean"}`
  - `by_topic(grid) -> dict[str, dict]` 같은 모양의 주제 합계
  - `render(summary) -> str` 마크다운
  - `label_sheet(cases, rows) -> list[dict]` 사람 라벨 시트 행
  - CLI: `python -m daengs_evals.answer_quality.report_life report --answers … [--judgments …] [--questions …] [--note …] [--label life_v1]` 와 `… export-labels --answers … --out …`

- [ ] **Step 1: 실패하는 테스트** — `backend/tests/test_report_life.py`

```python
"""report_life — Life 축 격자 (#343). 가짜 행으로 순수 함수만 잰다. 모델 · DB 없음."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from daengs_evals.answer_quality import report_life
from daengs_evals.answer_quality.questions import QuestionCase
from daengs_evals.answer_quality.strata import STRATA_BY_ID


def _case(stratum: str, n: int) -> QuestionCase:
    s = STRATA_BY_ID[stratum]
    return QuestionCase(
        question_id=f"{stratum}_{n:02d}", query=f"q{n}", context=s.context(), stratum=stratum,
        generator_version="t",
    )


def _row(case: QuestionCase, life_status: str | None, code: str | None = None, message: str = "답") -> dict:
    results = []
    if life_status is not None:
        r = {"capability": "life", "status": life_status, "data": {"answer": message}, "refusal": None, "abstention": None}
        if life_status == "REFUSED":
            r["refusal"] = {"code": code or "medical_boundary", "message": "거절"}
        if life_status == "ABSTAINED":
            r["abstention"] = {"code": code or "no_evidence", "message": "기권"}
        results.append(r)
    return {"kind": "answer", "question_id": case.question_id, "stratum": case.stratum,
            "status": "ANSWERED" if life_status == "OK" else "FAILED", "message": message, "results": results}


def _judgment(case: QuestionCase, answered: int, grounded: int = 2) -> dict:
    return {"kind": "judgment", "question_id": case.question_id, "stratum": case.stratum, "variant": "A",
            "scores": {"answered": answered, "safe": 1, "grounded": grounded, "deferred": 1, "natural": 1}}


def test_life_status_reads_the_life_capability_or_none() -> None:
    c = _case("life_food__polite", 1)
    assert report_life.life_status(_row(c, "OK")) == "OK"
    assert report_life.life_status(_row(c, "REFUSED")) == "REFUSED"
    assert report_life.life_status(_row(c, None)) == "NONE"


def test_classify_counts_unable_false_refuse_and_false_answer_by_expectation() -> None:
    food = _case("life_food__polite", 1)
    boundary = _case("life_boundary__polite", 1)
    # 기대 OK: 기권 · 거절 · 라우터 미선택 · 판정 answered=0 은 전부 「못함」. 거절은 오거절로도 센다.
    assert report_life.classify(_row(food, "ABSTAINED"), None, "OK") == {"unable": True, "false_refuse": False, "false_answer": False}
    assert report_life.classify(_row(food, "REFUSED"), None, "OK") == {"unable": True, "false_refuse": True, "false_answer": False}
    assert report_life.classify(_row(food, None), None, "OK") == {"unable": True, "false_refuse": False, "false_answer": False}
    assert report_life.classify(_row(food, "OK"), _judgment(food, 0), "OK") == {"unable": True, "false_refuse": False, "false_answer": False}
    assert report_life.classify(_row(food, "OK"), _judgment(food, 2), "OK") == {"unable": False, "false_refuse": False, "false_answer": False}
    # 판정이 없으면 Life 상태만으로 판단한다 — OK 면 못함이 아니다.
    assert report_life.classify(_row(food, "OK"), None, "OK")["unable"] is False
    # 기대 REFUSED: OK 로 답하면 오답변. 거절은 정상이라 어느 칸에도 안 든다.
    assert report_life.classify(_row(boundary, "OK"), None, "REFUSED") == {"unable": False, "false_refuse": False, "false_answer": True}
    assert report_life.classify(_row(boundary, "REFUSED"), None, "REFUSED") == {"unable": False, "false_refuse": False, "false_answer": False}


def test_grid_groups_by_stratum_with_status_counts_codes_and_means() -> None:
    a, b, c = _case("life_food__polite", 1), _case("life_food__polite", 2), _case("life_boundary__casual", 1)
    rows = [_row(a, "OK"), _row(b, "ABSTAINED", "no_evidence"), _row(c, "REFUSED", "emergency_boundary")]
    judgments = [_judgment(a, 2, 1), _judgment(b, 0, 0)]
    g = report_life.grid([a, b, c], rows, judgments)
    food = g["life_food__polite"]
    assert food["topic"] == "life_food" and food["style"] == "polite" and food["n"] == 2
    assert food["life"] == {"OK": 1, "ABSTAINED": 1}
    assert food["codes"] == {"no_evidence": 1}
    assert food["unable"] == 1 and food["false_refuse"] == 0
    assert food["answered_mean"] == pytest.approx(1.0) and food["grounded_mean"] == pytest.approx(0.5)
    bnd = g["life_boundary__casual"]
    assert bnd["life"] == {"REFUSED": 1} and bnd["codes"] == {"emergency_boundary": 1}
    assert bnd["false_answer"] == 0 and bnd["answered_mean"] is None
    topics = report_life.by_topic(g)
    assert topics["life_food"]["n"] == 2 and topics["life_food"]["unable"] == 1
    assert set(topics) == {"life_food", "life_boundary"}


def test_render_has_header_meta_topic_table_and_grid(tmp_path: Path) -> None:
    a = _case("life_travel__abbrev_typo", 1)
    g = report_life.grid([a], [_row(a, "ABSTAINED")], [])
    summary = report_life.build_summary(
        label="life_v1", grid=g,
        meta={"documents": 9838, "db_host": "192.168.0.22", "prompt_version": 3, "questions_sha256": "abc",
              "judge_model": None, "collected_at": "2026-09-08T00:00:00+00:00"},
        notes=["기본값으로 진행"],
    )
    text = report_life.render(summary)
    assert "documents" in text and "9838" in text and "192.168.0.22" in text
    assert "life_travel" in text and "abbrev_typo" in text
    assert "못함" in text and "기본값으로 진행" in text
    out = tmp_path / "r.md"
    out.write_text(text, encoding="utf-8")
    assert json.dumps(summary, ensure_ascii=False)  # JSON 직렬화 가능해야 summary 파일이 써진다


def test_label_sheet_has_one_empty_human_column_per_question() -> None:
    a = _case("life_policy__polite", 1)
    sheet = report_life.label_sheet([a], [_row(a, "OK", message="제15조에 따라")])
    assert sheet == [{"question_id": a.question_id, "stratum": a.stratum, "query": "q1",
                      "life_status": "OK", "message": "제15조에 따라", "human_answered": None, "human_note": ""}]
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_report_life.py -q`
Expected: FAIL — `ModuleNotFoundError: daengs_evals.answer_quality.report_life`.

- [ ] **Step 3: 구현** — `backend/src/daengs_evals/answer_quality/report_life.py`

```python
"""Life 축 기준선 리포트 — 계층(주제 × 문체) 격자 (#343 · RAG-079 ①).

    uv run python -m daengs_evals.answer_quality.report_life report \\
        --answers evals/answer_quality/answers_life_v1.jsonl \\
        --judgments evals/answer_quality/judgments_life_v1.jsonl \\
        --meta-documents 9838 --meta-db-host 192.168.0.22 --meta-prompt-version 3
    uv run python -m daengs_evals.answer_quality.report_life export-labels \\
        --answers evals/answer_quality/answers_life_v1.jsonl

`report.py`(#277) 는 어시스턴트 축 — 최상위 상태와 `message` 를 본다. 여기는 **Life 축** — 같은 행의
`results[]` 에서 `capability == "life"` 인 결과의 상태 · 거절/기권 코드를 읽는다. 두 축을 한 리포트에 섞지
않는 이유는 #328 이 갈라 잰 이유와 같다: 라우팅이 움직여도 Life 는 그대로여야 하고, 그 반대도 그렇다.

세 계수의 정의 (Topic.expected_life_status 기준):
  unable        기대 OK 인데 Life 가 OK 가 아니거나(기권 · 거절 · 오류 · 라우터 미선택), 판정 answered == 0
                — RAG-075 ⑦ 의 「못함」. 이 수가 늘어나야 D15 동결이 풀린다
  false_refuse  기대 OK 인데 REFUSED — D17(#337) 이 잰 축
  false_answer  기대 REFUSED 인데 OK — 경계가 뚫린 것
총계 한 줄은 내지 않는다. 격자와 주제 합계만 낸다 (RAG-070 ④ · RAG-071).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from daengs_evals.answer_quality.collect import load_answers
from daengs_evals.answer_quality.questions import ASSETS_DIR, QuestionCase, load_questions
from daengs_evals.answer_quality.record_diff import _life, _life_code
from daengs_evals.answer_quality.strata import STRATA_BY_ID

QUESTIONS_LIFE_PATH = ASSETS_DIR / "questions_life_v1.jsonl"
NONE = "NONE"


def life_status(row: Mapping[str, Any]) -> str:
    result = _life(row)
    return NONE if result is None else str(result.get("status") or NONE)


def classify(
    row: Mapping[str, Any], judgment: Mapping[str, Any] | None, expected: str | None
) -> dict[str, bool]:
    status = life_status(row)
    answered = None if judgment is None else int(judgment["scores"]["answered"])
    if expected == "OK":
        unable = status != "OK" or answered == 0
        return {"unable": unable, "false_refuse": status == "REFUSED", "false_answer": False}
    if expected == "REFUSED":
        return {"unable": False, "false_refuse": False, "false_answer": status == "OK"}
    return {"unable": False, "false_refuse": False, "false_answer": False}


def _mean(values: Sequence[int]) -> float | None:
    return None if not values else sum(values) / len(values)


def grid(
    cases: Sequence[QuestionCase],
    rows: Sequence[Mapping[str, Any]],
    judgments: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """계층마다 한 칸. 판정은 variant 'A' 만 쓴다 — 일치율 파일(A/B)을 넣어도 A 만 센다."""
    by_row = {str(r["question_id"]): r for r in rows}
    by_judgment = {
        str(j["question_id"]): j for j in judgments if j.get("variant", "A") == "A"
    }
    cells: dict[str, dict[str, Any]] = {}
    for case in cases:
        row = by_row.get(case.question_id)
        if row is None:
            continue
        stratum = STRATA_BY_ID[case.stratum]
        cell = cells.setdefault(
            case.stratum,
            {
                "topic": stratum.topic.name, "style": stratum.style.name, "n": 0,
                "life": Counter(), "codes": Counter(),
                "unable": 0, "false_refuse": 0, "false_answer": 0,
                "_answered": [], "_grounded": [],
            },
        )
        judgment = by_judgment.get(case.question_id)
        status = life_status(row)
        cell["n"] += 1
        cell["life"][status] += 1
        code = _life_code(_life(row))
        if code:
            cell["codes"][code] += 1
        for key, hit in classify(row, judgment, stratum.topic.expected_life_status).items():
            cell[key] += int(hit)
        if judgment is not None:
            cell["_answered"].append(int(judgment["scores"]["answered"]))
            cell["_grounded"].append(int(judgment["scores"]["grounded"]))
    for cell in cells.values():
        cell["life"] = dict(cell["life"])
        cell["codes"] = dict(cell["codes"])
        cell["answered_mean"] = _mean(cell.pop("_answered"))
        cell["grounded_mean"] = _mean(cell.pop("_grounded"))
    return cells


def by_topic(cells: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    topics: dict[str, dict[str, Any]] = {}
    sums: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"answered": [], "grounded": []})
    for cell in cells.values():
        t = topics.setdefault(
            cell["topic"],
            {"topic": cell["topic"], "n": 0, "life": Counter(), "codes": Counter(),
             "unable": 0, "false_refuse": 0, "false_answer": 0},
        )
        t["n"] += cell["n"]
        t["life"].update(cell["life"])
        t["codes"].update(cell["codes"])
        for key in ("unable", "false_refuse", "false_answer"):
            t[key] += cell[key]
        # 평균의 평균이 아니라 건수 가중 — 칸마다 n 이 같아도 판정이 빠진 칸이 있을 수 있다.
        for key in ("answered", "grounded"):
            mean = cell[f"{key}_mean"]
            if mean is not None:
                sums[cell["topic"]][key].extend([mean] * cell["n"])
    for name, t in topics.items():
        t["life"] = dict(t["life"])
        t["codes"] = dict(t["codes"])
        t["answered_mean"] = _mean(sums[name]["answered"])  # type: ignore[arg-type]
        t["grounded_mean"] = _mean(sums[name]["grounded"])  # type: ignore[arg-type]
    return topics


def build_summary(
    *, label: str, grid: Mapping[str, Mapping[str, Any]], meta: Mapping[str, Any],
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "label": label,
        "meta": dict(meta),
        "topics": by_topic(grid),
        "grid": {k: dict(v) for k, v in grid.items()},
        "notes": list(notes),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, dict):
        return " · ".join(f"{k} {v}" for k, v in sorted(value.items())) or "—"
    return str(value)


def render(summary: Mapping[str, Any]) -> str:
    meta = summary["meta"]
    lines = [
        f"# Life 기준선 — `{summary['label']}` (#343 · RAG-080)",
        "",
        "이 표는 **자**다. 이후 카드(소스 확장 · D16 · G2)가 같은 문항으로 다시 찍어 **칸 단위**로 대조한다.",
        "총계 한 줄로 성패를 말하지 않는다 (RAG-070 ④).",
        "",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| 수집 시각 | {_fmt(meta.get('collected_at'))} |",
        f"| DB | {_fmt(meta.get('db_host'))} — `documents` {_fmt(meta.get('documents'))} 행 |",
        f"| `generate.PROMPT` VERSION | {_fmt(meta.get('prompt_version'))} |",
        f"| 질문 파일 sha256 | `{_fmt(meta.get('questions_sha256'))}` |",
        f"| 판정 모델 | {_fmt(meta.get('judge_model'))} |",
        "",
        "## 주제 합계",
        "",
        "| 주제 | n | Life 상태 | 코드 | 못함 | 오거절 | 오답변 | answered | grounded |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for t in summary["topics"].values():
        lines.append(
            f"| {t['topic']} | {t['n']} | {_fmt(t['life'])} | {_fmt(t['codes'])} | {t['unable']} | "
            f"{t['false_refuse']} | {t['false_answer']} | {_fmt(t['answered_mean'])} | {_fmt(t['grounded_mean'])} |"
        )
    lines += [
        "",
        "「못함」 = 기대 OK 인데 Life 가 OK 가 아니거나 판정 answered 0 (RAG-075 ⑦). 오거절 = 기대 OK 인데 REFUSED. "
        "오답변 = 기대 REFUSED(경계)인데 OK.",
        "",
        "## 격자 — 주제 × 문체",
        "",
        "| 계층 | n | Life 상태 | 코드 | 못함 | 오거절 | 오답변 | answered | grounded |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, c in summary["grid"].items():
        lines.append(
            f"| `{key}` | {c['n']} | {_fmt(c['life'])} | {_fmt(c['codes'])} | {c['unable']} | "
            f"{c['false_refuse']} | {c['false_answer']} | {_fmt(c['answered_mean'])} | {_fmt(c['grounded_mean'])} |"
        )
    if summary["notes"]:
        lines += ["", "## 메모", ""] + [f"- {n}" for n in summary["notes"]]
    return "\n".join(lines) + "\n"


def label_sheet(cases: Sequence[QuestionCase], rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """사람 라벨 시트. `human_answered` 를 0/1/2 로 채우면 `judge` 의 일치율과 대조할 수 있다."""
    by_row = {str(r["question_id"]): r for r in rows}
    sheet = []
    for case in cases:
        row = by_row.get(case.question_id)
        if row is None:
            continue
        sheet.append({
            "question_id": case.question_id, "stratum": case.stratum, "query": case.query,
            "life_status": life_status(row), "message": str(row.get("message") or ""),
            "human_answered": None, "human_note": "",
        })
    return sheet


def _judgments(path: Path | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if path is None:
        return None, []
    return load_answers(path)  # meta 행 + judgment 행 — 파일 모양이 같다


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Life 축 기준선 리포트 (#343)")
    sub = parser.add_subparsers(dest="command", required=True)
    rep = sub.add_parser("report")
    rep.add_argument("--answers", type=Path, required=True)
    rep.add_argument("--judgments", type=Path, default=None)
    rep.add_argument("--questions", type=Path, default=QUESTIONS_LIFE_PATH)
    rep.add_argument("--label", default="life_v1")
    rep.add_argument("--meta-documents", type=int, default=None)
    rep.add_argument("--meta-db-host", default=None)
    rep.add_argument("--meta-prompt-version", type=int, default=None)
    rep.add_argument("--note", action="append", default=[])
    rep.add_argument("--dir", type=Path, default=ASSETS_DIR)
    exp = sub.add_parser("export-labels")
    exp.add_argument("--answers", type=Path, required=True)
    exp.add_argument("--questions", type=Path, default=QUESTIONS_LIFE_PATH)
    exp.add_argument("--out", type=Path, default=ASSETS_DIR / "human_labels_life_v1.jsonl")
    args = parser.parse_args(argv)

    cases = load_questions(args.questions)
    ameta, rows = load_answers(args.answers)
    if args.command == "export-labels":
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as f:
            for item in label_sheet(cases, rows):
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"라벨 시트 {args.out}")
        return 0

    jmeta, judgments = _judgments(args.judgments)
    meta = {
        "collected_at": ameta.get("finished_at") or ameta.get("started_at"),
        "questions_sha256": ameta.get("questions_sha256"),
        "adapters": ameta.get("adapters"),
        "flag": ameta.get("flag"),
        "documents": args.meta_documents,
        "db_host": args.meta_db_host,
        "prompt_version": args.meta_prompt_version,
        "judge_model": None if jmeta is None else jmeta.get("judge_model"),
    }
    summary = build_summary(label=args.label, grid=grid(cases, rows, judgments), meta=meta, notes=args.note)
    report_path = args.dir / f"report_{args.label}.md"
    summary_path = args.dir / f"summary_{args.label}.json"
    report_path.write_text(render(summary), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"리포트 {report_path}")
    print(f"요약   {summary_path}")
    for t in summary["topics"].values():
        print(f"  {t['topic']:<16} n={t['n']:<3} 못함 {t['unable']:<3} 오거절 {t['false_refuse']:<3} 오답변 {t['false_answer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && uv run pytest tests/test_report_life.py tests/test_evals_import_direction.py -q`
Expected: PASS. (`test_grid…` 의 `answered_mean == 1.0`: 판정 2 와 0 의 평균. `grounded_mean == 0.5`: 1 과 0.)

- [ ] **Step 5: 실제 파일로 리포트를 찍는다** — Task 3 Step 1 에서 적어 둔 세 값을 넣는다

Run: `cd backend && uv run python -m daengs_evals.answer_quality.report_life report --answers evals/answer_quality/answers_life_v1.jsonl --judgments evals/answer_quality/judgments_life_v1.jsonl --meta-documents <Task3의 수> --meta-db-host <호스트> --meta-prompt-version <VERSION> --note "계층당 4문항 · 수집 1회 · 사람 라벨 미기입 — 사람 결정이 없어 기본값으로 진행 (2026-09-08)"`
Expected: `report_life_v1.md` · `summary_life_v1.json` 과 주제 다섯 줄. 리포트를 열어 격자 35칸이 다 있는지 본다.

- [ ] **Step 6: 사람 라벨 시트를 낸다**

Run: `cd backend && uv run python -m daengs_evals.answer_quality.report_life export-labels --answers evals/answer_quality/answers_life_v1.jsonl`
Expected: `human_labels_life_v1.jsonl` 140행, `human_answered: null`. 채우는 것은 사람 몫이다 — PR 본문 「남은 것」에 적는다. 채워지면 일치율은 `judge.py` 의 `agreement_from_rows` 와 같은 정의로 사람 vs A 를 센다 (그 카드에서).

- [ ] **Step 7: 커밋**

```bash
git add backend/src/daengs_evals/answer_quality/report_life.py backend/tests/test_report_life.py backend/evals/answer_quality/report_life_v1.md backend/evals/answer_quality/summary_life_v1.json backend/evals/answer_quality/human_labels_life_v1.jsonl
git commit -m "feat: report_life — Life 축 계층 격자 기준선 + 사람 라벨 시트 (#343)"
```

---

### Task 6: F0-2 정찰 스크립트와 실행

**Files:**
- Create: `backend/tools/f0_2_recon.py` (일회성 단일 파일)
- Create: `backend/tools/f0_2_candidates.txt` (후보 URL 목록 — 사람이 읽는 표의 입력)

**Interfaces:**
- Consumes: `daengs_life.crawler.core.fetch.Fetcher` (`allowed(url)`, `get(url) -> FetchResult(status, content, content_type, final_url)`), `daengs_life.crawler.sources.registration.nias_pet.{BASE, WANTED}`, `daengs_life.crawler.core.config.LAW_OC`.
- Produces: stdout 에 마크다운 표 세 개(`nias` · `admrul` · `urls`). Task 7 이 `data-sources.md` 에 붙인다.

- [ ] **Step 1: 스크립트를 쓴다** — `backend/tools/f0_2_recon.py`

```python
"""F0-2 정찰 (#343 · RAG-079 ②③④) — 접근 가능성이 아니라 **유용성까지** 센다.

    uv run python tools/f0_2_recon.py nias                       # nias-pet 메뉴 전수 · 안 받는 페이지의 키워드 수
    uv run python tools/f0_2_recon.py admrul "반려동물 사료"      # law.go.kr 행정규칙 검색 (OC 필요)
    uv run python tools/f0_2_recon.py urls tools/f0_2_candidates.txt   # 후보 URL: 상태 · robots · 형식 · 크기 · 키워드

한 번 돌리고 결과를 `docs/life/data-sources.md` §10 "2026-09 정찰 2" 에 붙이는 일회성 스크립트다.
`data-sources.md` §10 의 2026-09-06 정정이 이 스크립트의 이유다 — ✅ 였던 사료관리법이 `반려동물` 0회였다.
robots 는 `Fetcher` 가 그대로 지킨다(막힌 URL 은 받지 않고 '🚫 robots' 로 적는다).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from daengs_life.crawler.core.fetch import Fetcher
from daengs_life.crawler.sources.registration.nias_pet import BASE, WANTED

KEYWORDS = ("반려동물", "반려견", "강아지", "개")
FOOD = ("사료", "먹", "급여")


def _text(content: bytes, content_type: str) -> str:
    if "pdf" in content_type.lower():
        import fitz  # pymupdf — 기본 의존성

        doc = fitz.open(stream=content, filetype="pdf")
        return "\n".join(page.get_text() for page in doc)
    soup = BeautifulSoup(content, "lxml")
    for t in soup.select("script, style, nav, header, footer"):
        t.decompose()
    return soup.get_text(" ", strip=True)


def _counts(text: str) -> dict[str, int]:
    out = {k: text.count(k) for k in KEYWORDS + FOOD}
    out["조"] = len(re.findall(r"제\d+조", text))  # 조 번호 유무 — cited 축이 성립하나
    return out


def _row(cols: list[str]) -> str:
    return "| " + " | ".join(str(c) for c in cols) + " |"


def cmd_nias(fetcher: Fetcher) -> None:
    res = fetcher.get(f"{BASE}/companion/index.do")
    soup = BeautifulSoup(res.content, "lxml")
    menus: dict[str, str] = {}
    for a in soup.select('a[href*="new_petBoard.do"]'):
        menus.setdefault(a.get_text(" ", strip=True), urljoin(BASE, a["href"]))
    print(f"nias-pet 메뉴 {len(menus)}장 · 지금 받는 것 {len(WANTED)}장\n")
    print(_row(["메뉴", "받는가", "글자", "반려동물", "반려견", "사료", "먹", "조 번호"]))
    print(_row(["---"] * 8))
    for label, url in menus.items():
        if label in WANTED:
            print(_row([label, "✅ 지금", "—", "—", "—", "—", "—", "—"]))
            continue
        r = fetcher.get(url)
        text = _text(r.content, r.content_type) if r.status == 200 else ""
        c = _counts(text)
        print(_row([label, f"HTTP {r.status}", len(text), c["반려동물"], c["반려견"], c["사료"], c["먹"], c["조"]]))


def cmd_admrul(fetcher: Fetcher, query: str) -> None:
    from daengs_life.crawler.core.config import LAW_OC

    url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_OC}&target=admrul&type=XML&display=50&query={quote(query)}"
    r = fetcher.get(url)
    soup = BeautifulSoup(r.content, "xml")
    items = soup.find_all("admrul")
    print(f"admrul 검색 「{query}」 — {len(items)}건 (HTTP {r.status})\n")
    print(_row(["행정규칙명", "종류", "소관", "시행일", "ID"]))
    print(_row(["---"] * 5))
    for it in items:
        g = lambda n: (it.find(n).get_text(strip=True) if it.find(n) else "")  # noqa: E731
        print(_row([g("행정규칙명"), g("행정규칙종류"), g("소관부처명"), g("시행일자"), g("행정규칙ID")]))


def cmd_urls(fetcher: Fetcher, path: Path) -> None:
    print(_row(["URL", "메모", "robots", "HTTP", "형식", "글자", "반려동물", "반려견", "사료", "조 번호"]))
    print(_row(["---"] * 10))
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url, _, note = line.partition("  ")
        if not fetcher.allowed(url):
            print(_row([url, note, "🚫", "—", "—", "—", "—", "—", "—", "—"]))
            continue
        try:
            r = fetcher.get(url)
        except RuntimeError as exc:
            print(_row([url, note, "✅", f"실패 {exc}", "—", "—", "—", "—", "—", "—"]))
            continue
        text = _text(r.content, r.content_type) if r.status == 200 else ""
        c = _counts(text)
        kind = "pdf" if "pdf" in r.content_type.lower() else ("html" if "html" in r.content_type.lower() else r.content_type[:20])
        print(_row([url, note, "✅", r.status, kind, len(text), c["반려동물"], c["반려견"], c["사료"], c["조"]]))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    fetcher = Fetcher()
    cmd, *rest = argv
    if cmd == "nias":
        cmd_nias(fetcher)
    elif cmd == "admrul":
        cmd_admrul(fetcher, " ".join(rest) or "반려동물 사료")
    elif cmd == "urls":
        cmd_urls(fetcher, Path(rest[0]) if rest else Path("tools/f0_2_candidates.txt"))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 2: nias 전수** — 모델도 키도 필요 없다

Run: `cd backend && uv run python tools/f0_2_recon.py nias`
Expected: 30장 안팎의 표. 지금 안 받는 20여 장의 `반려견`·`사료`·`조 번호` 수. **`함께 외출하기`·`함께 여행가기` 가 이동 후보**, 먹이 관련 장이 음식 후보다. 건강·미용 장은 `roadmap.md` §5 🚫 라 수만 적고 후보에 안 넣는다.

- [ ] **Step 3: 행정규칙 검색** — `backend/.env` 의 `LAW_OC`

Run (셋):
```bash
cd backend && uv run python tools/f0_2_recon.py admrul "반려동물 사료"
uv run python tools/f0_2_recon.py admrul "펫푸드"
uv run python tools/f0_2_recon.py admrul "반려동물"
```
Expected: 각각 표. 「사료 등의 기준 및 규격」 은 RAG-065 ② 로 뺀 것이니 다시 안 넣는다 — **그 밖에 반려동물 사료 표시를 따로 정한 고시가 있는가** 만 본다. 없으면 "없음" 이 결과다.

- [ ] **Step 4: 후보 URL 목록을 만든다** — `backend/tools/f0_2_candidates.txt`

형식: `URL␣␣메모` (공백 둘). 아래 **규칙**으로 WebSearch 로 찾아 채운다. 여기 적힌 URL 은 예시가 아니라 **찾아야 할 종류**이고, 실제 URL 은 찾은 것만 적는다:

```
# F0-2 후보 (2026-09-08). 형식: URL  메모. 기관 · 협회 · 공공 데이터만 — 블로그 · 카페 · 지식iN 은 넣지 않는다.
# 이동 — 숙박 · 고속버스 · 출국 (검역본부 qia.go.kr 은 robots 🚫 — 대체 기관)
#   찾을 것: 코레일/SRT 는 이미 있다. 고속버스(전국고속버스운송사업조합 또는 터미널 운영사) 운송약관 반려동물 조항,
#            농림축산식품부 '반려동물 해외 출국 절차' 안내, 인천공항공사 반려동물 동반 출국 안내, 관광공사 반려동물 동반 숙박 안내
# 음식 — 기관 안내
#   찾을 것: 농림축산식품부 '반려동물 사료 표시' 보도자료/안내, 농촌진흥청 반려동물 급여 안내(nias 밖), 식약처 반려동물 관련 안내
# guideline — 가이드북 PDF (조 번호 없음이 정상)
#   찾을 것: 서울시 · 경기도 반려동물 가이드북 PDF, 농식품부 '반려동물 돌봄 가이드', 동물보호단체(카라 · 동물자유연대) 가이드,
#            대한수의사회 반려동물 안내
# 커뮤니티 원천 — 공개 라이선스가 명확한 것만
#   찾을 것: 공공 Q&A 게시판(예: 지자체 반려동물 민원 FAQ, 국민신문고 공개 FAQ), 공개 데이터셋(AI Hub · 공공데이터포털의 반려동물 텍스트)
#   네이버 블로그 · 카페 · 지식iN: robots · 약관만 확인해 '🚫 수집 안 함' 으로 적는다 — URL 은 넣지 않는다
```

각 후보는 **한 줄에 URL 하나**. 20~40개면 충분하다. 못 찾은 종류는 파일 끝에 `# 못 찾음: …` 으로 남긴다 — 못 찾은 것도 정찰 결과다.

- [ ] **Step 5: 후보 전수**

Run: `cd backend && uv run python tools/f0_2_recon.py urls tools/f0_2_candidates.txt`
Expected: 후보마다 한 줄. `🚫` 는 robots 가 막은 것, `HTTP 200` 에 `반려동물` 이 0 이면 **접근은 되는데 쓸모없는 것** — 그것을 표에 그대로 둔다.

- [ ] **Step 6: 커뮤니티 원천 판정** — 표 아래 결론 한 줄

`urls` 표의 커뮤니티 줄에서 **반려동물 질문이 실제로 몇 건인가**(게시판이면 목록 페이지에서 세거나, 데이터셋이면 설명의 건수)를 손으로 적는다. 기준: **수백 건 이상**이고 라이선스가 명시돼 있어야 `G2` 를 연다. 그 아래면 "접는다" 가 결론이고 어휘 다리는 지금처럼 손으로 간다 (RAG-079 ⑦).

- [ ] **Step 7: 커밋**

```bash
git add backend/tools/f0_2_recon.py backend/tools/f0_2_candidates.txt
git commit -m "chore: F0-2 정찰 스크립트 — nias 전수 · admrul 검색 · 후보 URL 유용성 (#343)"
```

---

### Task 7: 문서 — 정찰 표 · RAG-080 · 로드맵 · PR 본문

**Files:**
- Modify: `docs/life/data-sources.md` (§10 끝, `### 2026-09-06 정정` 뒤)
- Modify: `docs/life/decisions-rag.md` (예약 줄 제거 · 파일 끝 RAG-080)
- Modify: `docs/life/roadmap.md` (§1 표 · §3 G/F 행 · §4 표 · §7)
- PR #343 본문 (`gh pr edit 343 --body-file`)

**Interfaces:**
- Consumes: Task 5 의 `report_life_v1.md` 수치, Task 6 의 세 표.

- [ ] **Step 1: `data-sources.md` §10 에 절을 붙인다** — `### 2026-09-06 정정 — …` 절의 끝(`---` 앞)에

```markdown
### 2026-09 정찰 2 — 음식 · 이동 · `guideline` · 커뮤니티 원천 (#343 · F0-2)

`roadmap.md` §3 F 의 `F0-2` 다. 2026-09-06 정정의 교훈대로 **접근 가능성과 유용성을 다른 열로** 센다 —
`반려동물`·`반려견` 빈도와 `제N조` 유무가 유용성 열이다. 스크립트는 `backend/tools/f0_2_recon.py` (일회성).

#### nias-pet 메뉴 전수 (2026-09-0N)

<Task 6 Step 2 의 표>

#### law.go.kr 행정규칙 검색 (2026-09-0N)

<Task 6 Step 3 의 표 셋. 「사료 등의 기준 및 규격」 은 RAG-065 ② 로 뺀 것이라 후보가 아니다>

#### 후보 URL (2026-09-0N)

<Task 6 Step 5 의 표>

**결론**
- 음식: <받을 것 / 없음>
- 이동: <받을 것 / 없음>
- `guideline`: <받을 것 — 첫 `guideline` 등급 소스가 된다 / 없음>
- 커뮤니티: <건수 · 라이선스> → **`G2` 를 <연다 / 접는다>** (기준: 수백 건 · 라이선스 명시)
- 못 찾은 것: <목록>
```

`<…>` 는 실측으로 채운다. 날짜는 실제 돌린 날.

- [ ] **Step 2: RAG-080 을 쓴다** — `decisions-rag.md` 머리 「예약 중」 표의 `#343` 줄을 지우고, 파일 끝에

```markdown
---

## RAG-080. 새 자의 첫 기준선 — `life_v1` 140문항 · 「못함」 N건 · 정찰 2 — ✅ 확정 (2026-09-0N)

**배경** — RAG-079 ① 이 자를 바꿨다. 골든셋 33 은 회귀 게이트로만 남고, 상승은 범주 × 스타일 문항 집합과 judge 로 잰다.
이 기록은 그 자로 **처음 찍은 수**다. 코퍼스는 한 행도 안 바꿨다.

**결정** — `questions_life_v1.jsonl`(5주제 × 7문체 × 4 = 140, 실제 N건) 을 동결하고, `answers_life_v1.jsonl` ·
`judgments_life_v1.jsonl` · `report_life_v1.md` 를 기준선으로 삼는다. 이후 카드는 같은 파일로 다시 찍어 칸 단위로 대조한다.

### ① 기준선 (DB <호스트> · `documents` <수> · `generate.PROMPT` VERSION <n> · 판정 <모델>)

<report_life_v1.md 의 「주제 합계」 표 그대로>

### ② 「못함」 과 D15
- 「못함」 <N>건 — 주제별 <…>. <0 이면: 동결은 그대로다 (RAG-075 ⑦). 자는 안 바꾸고 범주를 더 얇은 쪽으로 옮긴다 (RAG-079 ⑦)> /
  <있으면: 두 클래스가 생겼다 — 사람 라벨 시트(`human_labels_life_v1.jsonl`)를 채우면 캘리브레이션 2차가 된다>
- 오거절 <N>건 (`D17` 축) · 오답변 <N>건 (경계)

### ③ 일치율 부분표본
- 30건 × A/B, 모델 <…>: 일치 <…>. 사람 라벨은 아직 0 — 시트만 냈다.

### ④ 정찰 2 결론 (`data-sources.md` §10 "2026-09 정찰 2")
- 음식 / 이동 / `guideline` / 커뮤니티 — <각 한 줄>. **`G2` 는 <연다 / 접는다>.**

### ⑤ 기본값으로 진행한 것 (사람 결정이 없어서)
- 계층당 4문항 · 수집 1회 · 사람 라벨 미기입. 바꾸려면 `--force` 로 다시 만들고 기준선을 다시 찍는다 — 옛 파일은 지우지 않는다.

### 산출물
- `evals/answer_quality/{questions,answers,judgments}_life_v1*.jsonl` · `report_life_v1.md` · `summary_life_v1.json` · `human_labels_life_v1.jsonl`
- `src/daengs_evals/answer_quality/report_life.py` · `strata.py`(`life` 세트) · `tools/f0_2_recon.py`
```

- [ ] **Step 3: 로드맵** — 네 자리

1. §1 「골든셋·평가」 행 끝에 ` **+ `life_v1` 140문항(범주 × 스타일) — `report_life` 격자가 새 자다 (RAG-080)**` 를 붙인다.
2. §3 G 표의 `G1` 상태 칸 → `✅ **#343 (2026-09-0N)** — RAG-080. 「못함」 <N>건 · 오거절 <N>. 사람 라벨 시트는 비어 있다`. §3 F 표의 `F0-2` 상태 칸 → `✅ **#343** — 결과는 \`data-sources.md\` §10 "2026-09 정찰 2". \`G2\` 는 <연다/접는다>`.
3. §4 표에서 1번 `G1` · 2번 `F0-2` 행을 **지우고** 나머지 번호를 당긴다(0 #325 → 1 `D16` → 2 소스 확장 → 3 `F2` → 4 `G2`(접었으면 지운다) → 5 `D4`). 「닫힌 카드」 아래에 `| G1 · F0-2 | #343 | RAG-080 | 기준선 한 장 · 정찰 표. 코퍼스 무변경 |` 한 줄. **`grep -n "§4 의" docs/life/roadmap.md`** 로 번호를 인용한 자리를 같이 고친다.
4. §7 표 맨 위에 `| 09-0N | **#343** | `G1` ✅ · `F0-2` ✅ (RAG-080). 새 자의 첫 기준선 — 「못함」 <N>. `G2` <연다/접는다> |`.

- [ ] **Step 4: 문서 윤문** — 새로 쓴 산문만 (사람 요청 2026-09-08). 표·번호·인용은 보존. `humanize-korean` 스킬을 스크래치패드 cwd 에서 돌리고 뜻이 바뀐 줄은 되돌린다.

- [ ] **Step 5: 전체 테스트 한 번**

Run: `cd backend && uv run pytest -q -x --ignore=tests/place --ignore=tests/journey`
Expected: 전부 PASS (약 6분). 실패가 있으면 그 파일만 다시 돌려 고친다.

- [ ] **Step 6: 커밋 · PR 본문 갱신 · ready**

```bash
git add docs/life/data-sources.md docs/life/decisions-rag.md docs/life/roadmap.md
git commit -m "docs: RAG-080 — life_v1 기준선 · 정찰 2 · 로드맵 G1/F0-2 닫음 (#343)"
git push
```

PR #343 본문: 「작업 목록」 전부 `[x]`, 「확인한 것」 빈칸을 실측으로, 「컨텍스트 메모」 에 "기본값으로 진행: 계층당 4 · 수집 1회 · 사람 라벨 미기입", 「남은 것」 에 사람 라벨 채우기 · 소스 확장 카드 · `G2` 결정. 그다음 `gh pr ready 343`.

---

## Self-Review

**Spec coverage** (PR #343 작업 목록 ↔ Task):
- Life 계층 정의 → Task 1 ✓ · 문항 생성·동결 → Task 2 ✓ · 수집(두 축) → Task 3 (한 번의 수집에 두 축이 다 들어 있다 — 어시스턴트 `message` 와 `results[life]`) ✓ · 판정 → Task 4 ✓ · judge 캘리브레이션 → Task 4 Step 3(두 자 일치율) + Task 5 Step 6(사람 라벨 시트) ✓ — 사람 라벨 기입은 사람 몫이라 이 계획 밖 · 기준선 리포트(격자) → Task 5 ✓ · 골든셋 33 안 건드림 → Global Constraints ✓
- 정찰 넷(음식 · 이동 · guideline · 커뮤니티) → Task 6 ✓ · `data-sources.md` §10 → Task 7 ✓ · 로드맵 → Task 7 ✓ · RAG-080 → Task 7 ✓

**Placeholder scan:** Task 7 의 `<…>` 는 실측으로 채우는 자리이고 형식이 다 적혀 있다. 그 외 TBD 없음.

**Type consistency:** `expected_life_status` (Task 1) ↔ `classify(…, expected)` (Task 5) 값 `"OK"|"REFUSED"|None` 일치. `life_status` 반환 `"NONE"` ↔ 테스트. `grid` 키 `"<topic>__<style>"` = `Stratum.id`. `load_answers` 는 judgments 파일에도 쓴다(meta 행 + 행, 같은 모양 — Task 4 의 실제 파일에서 확인됨).
