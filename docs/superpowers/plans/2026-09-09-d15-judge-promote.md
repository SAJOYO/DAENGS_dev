# D15 동결 해제 — 사람 라벨 30 과 judge 승격 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 2026-09-08 에 사람이 매긴 `answered` 라벨 30건을 저장소에 넣고, 사람 대 judge 일치율을 파일로 낼 수 있게 만들고, `D15`(LLM judge) 동결을 푼다.

**Architecture:** 새 모듈을 만들지 않는다. 라벨은 이미 있는 `human_labels_life_v1.jsonl` 의 `human_answered`·`human_note` 칸을 채우는 것이고, 일치율은 `report_life.py` 에 `agreement` 서브커맨드 하나를 더해 `judge.agreement_rates()` 를 재사용한다. 루브릭 프롬프트는 한 글자도 안 건드린다.

**Tech Stack:** Python 3.12 · uv · pytest. **모델 호출 없음 · DB 접근 없음.**

**Spec:** PR #348 본문 · `docs/life/decisions-rag.md` RAG-075 ⑦(동결 이유) · RAG-080 ②(해제 조건이 찼다) · RAG-007(judge 원안)

## Global Constraints

- **모델을 부르지 않는다.** 이 카드는 이미 있는 판정 파일과 사람 라벨만 쓴다. `judge score`·`collect_life`·`rag generate` 를 돌리지 않는다.
- 🔴 **`judge.py` 의 루브릭 프롬프트(`_RUBRIC_A`·`_RUBRIC_B`)와 `JUDGE_PROMPT_VERSIONS` 를 고치지 않는다.** 고치면 #277 의 84건과 축이 갈린다. 관대함 보정은 **이 카드에서 안 한다** — 관찰만 기록한다(사람 결정).
- **라벨 30건의 원본은 아티팩트 db 에서 받아 `.superpowers/sdd/2026-09-09-d15/labels/` 에 저장돼 있다** (30개 JSON, 키: `answered`·`note`·`stratum`·`life_status`·`at`). 저장소로 옮기는 순간부터 jsonl 이 정본이다.
- **30건은 `judgments_life_v1_direct_agreement.jsonl` 의 부분표본과 같은 문항이어야 한다** — 다른 문항이면 A/B 대조가 성립하지 않는다. Task 1 이 그것을 검사한다.
- 두 judge 를 합치지 않는다 — `rag judge`(골든셋 33, OpenAI 계열)와 `answer_quality.judge`(루브릭, Gemini)는 별개다 (RAG-074).
- 의존성 추가 금지. 운영 코드는 `daengs_evals` 를 import 하지 않는다.
- 커밋 메시지 끝: `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` + `Claude-Session: https://claude.ai/code/session_014MevWGJ9PMs6mjWDZvwcis`. 제목에 `(#348)`.
- **push 는 컨트롤러가 한다.** 하위 에이전트는 커밋까지. **`git stash` 금지**(스택이 워크트리 간 공유다).
- 테스트는 `cd backend && uv run pytest <파일> -q`. 전체는 마지막 한 번.
- ⚠ `decisions-rag.md` 는 #347 브랜치도 파일 끝에 항목을 붙였다. 두 카드가 머지될 때 파일 끝에서 충돌한다 — 나중에 머지하는 쪽이 두 항목을 나란히 두면 된다. 이 카드에서 해결할 것이 아니다.

---

## File Structure

| 파일 | 책임 | 상태 |
| --- | --- | --- |
| `backend/evals/answer_quality/human_labels_life_v1.jsonl` | 30행의 `human_answered`·`human_note`·`human_at` | 수정 |
| `backend/src/daengs_evals/answer_quality/report_life.py` | `agreement` 서브커맨드 + 순수 함수 | 수정 |
| `backend/tests/test_report_life.py` | 일치율 함수 테스트 | 수정 |
| `backend/evals/answer_quality/agreement_life_v1.md` · `.json` | 산출물 | 생성 |
| `docs/life/decisions-rag.md` · `docs/life/roadmap.md` | RAG-082 · `D15` ✅ | 수정 |

---

### Task 1: 라벨 30건을 저장소로

**Files:**
- Modify: `backend/evals/answer_quality/human_labels_life_v1.jsonl`
- Test: `backend/tests/test_report_life.py`

**Interfaces:**
- Produces: 140행 중 30행이 `human_answered ∈ {0,1,2}` · `human_note` 문자열 · 새 칸 `human_at`(ISO8601). 나머지 110행은 `human_answered: null` 그대로.

- [ ] **Step 1: 실패하는 테스트** — `backend/tests/test_report_life.py` 끝에

```python
def test_human_label_sheet_has_thirty_filled_rows_matching_the_agreement_subsample() -> None:
    """사람 라벨 30건 (#348). 일치율 부분표본과 같은 문항이라야 A/B 와 대조된다."""
    import json

    from daengs_evals.answer_quality.questions import ASSETS_DIR

    rows = [json.loads(l) for l in (ASSETS_DIR / "human_labels_life_v1.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 140
    filled = [r for r in rows if r["human_answered"] is not None]
    assert len(filled) == 30
    assert all(r["human_answered"] in (0, 1, 2) for r in filled)
    assert all(isinstance(r.get("human_at"), str) and r["human_at"] for r in filled)
    # 채운 문항 = 일치율 부분표본의 문항
    judged = {
        json.loads(l)["question_id"]
        for l in (ASSETS_DIR / "judgments_life_v1_direct_agreement.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip() and json.loads(l).get("kind") == "judgment"
    }
    assert {r["question_id"] for r in filled} == judged
    # 안 채운 행은 손대지 않았다
    assert all(r["human_answered"] is None and r["human_note"] == "" for r in rows if r["question_id"] not in judged)
```

- [ ] **Step 2: 실패 확인** — `cd backend && uv run pytest tests/test_report_life.py -q -k human_label` → FAIL (`len(filled) == 30` 에서 0).

- [ ] **Step 3: 스크립트로 채운다** (손편집 금지). `.superpowers/sdd/2026-09-09-d15/labels/*.json` 의 파일명이 `question_id` 다. 각 행에 `human_answered = <answered>` · `human_note = <note>` · `human_at = <at>` 를 넣고, 나머지 필드와 행 순서·나머지 110행은 그대로 둔다. 저장한 라벨의 `stratum`·`life_status` 가 시트의 값과 다르면 **STOP 하고 보고** — 다른 실행의 라벨이라는 뜻이다.

- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_report_life.py -q` PASS.

- [ ] **Step 5: 커밋**

```bash
git add backend/evals/answer_quality/human_labels_life_v1.jsonl backend/tests/test_report_life.py
git commit -m "chore: 사람 라벨 30건 — human_labels_life_v1.jsonl (#348)"
```

---

### Task 2: `report_life agreement`

**Files:**
- Modify: `backend/src/daengs_evals/answer_quality/report_life.py`
- Test: `backend/tests/test_report_life.py`
- Create (CLI 산출): `backend/evals/answer_quality/agreement_life_v1.md` · `summary_agreement_life_v1.json`

**Interfaces:**
- Consumes: `judge.agreement_rates(scores_a, scores_b) -> {"question_count", "threshold", "rates": {item: float|None}, "excluded_items": [...]}`; 라벨 시트 행(`question_id`·`human_answered`·`human_note`·`stratum`·`life_status`); 판정 행(`question_id`·`variant`·`scores`·`judge_model`).
- Produces:
  - `human_vs_judge(labels, judgments) -> dict` — 키 `n`, `pairs` (variant → {qid: {"human": int, "judge": int}}), `agreement` (variant → {"same": int, "within1": int, "rate": float}), `confusion` (variant → {"h,j": count}), `disagreements` (list of {question_id, stratum, life_status, human, judge_a, judge_b, note}), `judge_models` (list), `ab` (`agreement_rates` 결과 그대로 — `answered` 항목만 쓰지만 표 전체를 싣는다)
  - `render_agreement(summary) -> str` — 마크다운(표 셋: 대조 요약 · 혼동 · 불일치 목록)
  - CLI: `python -m daengs_evals.answer_quality.report_life agreement --labels <jsonl> --judgments <agreement jsonl> [--label life_v1] [--dir <ASSETS_DIR>]`

- [ ] **Step 1: 실패하는 테스트** — `backend/tests/test_report_life.py` 에

```python
def _label(qid: str, human: int, note: str = "") -> dict:
    return {"question_id": qid, "stratum": "life_food__polite", "query": "q", "life_status": "OK",
            "message": "m", "human_answered": human, "human_note": note, "human_at": "2026-09-08T00:00:00Z"}


def _judgment_row(qid: str, variant: str, answered: int) -> dict:
    return {"kind": "judgment", "question_id": qid, "stratum": "life_food__polite", "variant": variant,
            "judge_model": "m-" + variant,
            "scores": {"answered": answered, "safe": 1, "grounded": 2, "deferred": 1, "natural": 1}}


def test_human_vs_judge_counts_matches_within_one_and_confusion() -> None:
    labels = [_label("q1", 2), _label("q2", 1, "요지에서 벗어난 답이 길다"), _label("q3", 1), _label("q4", 2)]
    judgments = [
        _judgment_row("q1", "A", 2), _judgment_row("q1", "B", 2),
        _judgment_row("q2", "A", 0), _judgment_row("q2", "B", 1),
        _judgment_row("q3", "A", 2), _judgment_row("q3", "B", 2),
        _judgment_row("q4", "A", 2), _judgment_row("q4", "B", 2),
    ]
    out = report_life.human_vs_judge(labels, judgments)
    assert out["n"] == 4
    assert out["agreement"]["A"] == {"same": 2, "within1": 4, "rate": 0.5}
    assert out["agreement"]["B"] == {"same": 3, "within1": 4, "rate": 0.75}
    assert out["confusion"]["A"] == {"1,0": 1, "1,2": 1, "2,2": 2}
    ids = [d["question_id"] for d in out["disagreements"]]
    assert ids == ["q2", "q3"]           # 사람과 A 가 다른 문항만, 시트 순서대로
    assert out["disagreements"][0]["note"] == "요지에서 벗어난 답이 길다"
    assert out["disagreements"][0]["judge_b"] == 1
    assert out["judge_models"] == ["m-A", "m-B"]
    # A/B 표는 judge.agreement_rates 를 그대로 쓴다 — 항목 다섯이 다 있다
    assert set(out["ab"]["rates"]) == {"answered", "safe", "grounded", "deferred", "natural"}
    assert out["ab"]["question_count"] == 4


def test_human_vs_judge_ignores_unlabelled_rows_and_unshared_questions() -> None:
    labels = [_label("q1", 2), {**_label("q9", 0), "human_answered": None}]
    judgments = [_judgment_row("q1", "A", 2), _judgment_row("q1", "B", 2), _judgment_row("q8", "A", 0)]
    out = report_life.human_vs_judge(labels, judgments)
    assert out["n"] == 1 and out["agreement"]["A"]["same"] == 1


def test_render_agreement_has_the_three_tables_and_the_models() -> None:
    labels = [_label("q1", 2), _label("q2", 1, "메모")]
    judgments = [_judgment_row("q1", "A", 2), _judgment_row("q1", "B", 2),
                 _judgment_row("q2", "A", 2), _judgment_row("q2", "B", 1)]
    text = report_life.render_agreement(report_life.human_vs_judge(labels, judgments), label="life_v1")
    assert "사람 대 judge" in text and "혼동" in text and "불일치" in text
    assert "m-A" in text and "q2" in text and "메모" in text
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_report_life.py -q -k "human_vs_judge or render_agreement"` → FAIL (`AttributeError: human_vs_judge`).

- [ ] **Step 3: 구현** — `report_life.py` 에 더한다. `judge.agreement_rates` 를 import 해서 A/B 표는 재사용하고, 사람 대 judge 는 새로 센다. `rate` 는 `round(same/n, 4)`. `confusion` 키는 `f"{human},{judge}"`. `disagreements` 는 **시트 행 순서**를 지킨다(라벨 파일 순서). 도크스트링에 이 카드가 무엇을 승격하는지와 두 judge 를 안 합친다는 것을 적는다.
  CLI 서브커맨드 `agreement` 는 `--labels` · `--judgments` 를 받아 `agreement_<label>.md` 와 `summary_agreement_<label>.json` 을 `--dir`(기본 `ASSETS_DIR`)에 쓰고, 요약 줄을 stdout 에 찍는다.

- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_report_life.py tests/test_evals_import_direction.py -q` PASS. `uv run ruff check src/daengs_evals/answer_quality/report_life.py tests/test_report_life.py`.

- [ ] **Step 5: 실제 파일로 산출**

```bash
cd backend && PYTHONIOENCODING=utf-8 uv run python -m daengs_evals.answer_quality.report_life agreement \
  --labels evals/answer_quality/human_labels_life_v1.jsonl \
  --judgments evals/answer_quality/judgments_life_v1_direct_agreement.jsonl --label life_v1
```
Expected: 사람=A 26/30 · 사람=B 27/30 · A=B 27/30 · 1점 이내 30/30. **수가 다르면 STOP 하고 보고** — 라벨이나 판정 파일이 예상과 다르다는 뜻이다.

- [ ] **Step 6: 커밋**

```bash
git add backend/src/daengs_evals/answer_quality/report_life.py backend/tests/test_report_life.py backend/evals/answer_quality/agreement_life_v1.md backend/evals/answer_quality/summary_agreement_life_v1.json
git commit -m "feat: report_life agreement — 사람 라벨 대 judge 일치율 (#348)"
```

---

### Task 3: 문서 — RAG-082 와 `D15` 해제

**Files:** `docs/life/decisions-rag.md` · `docs/life/roadmap.md`

- [ ] **Step 1: `decisions-rag.md`** — 「예약 중」 표의 `#348` 행을 지우고, 파일 끝 `---` 뒤에 **RAG-082**:
  - 제목: `## RAG-082. 사람 라벨 30 이 judge 를 통과시켰다 — `D15` 동결을 푼다 — ✅ 확정 (2026-09-09)`
  - **배경** — #315 가 동결한 이유(판정 32/33 답함, 한 클래스 97%, 변별력 못 잼 — RAG-075 ⑦)와 그것을 바꾼 것(#343 의 새 자에서 「못함」 35건 → 두 클래스 — RAG-080 ②).
  - **① 수** — Task 2 의 표. 사람=A 26/30 · 사람=B 27/30 · A=B 27/30 · 1점 이내 30/30. 혼동표.
  - **② 불일치 넷은 한 방향이다** — 전부 사람 1 · judge 2(또는 0). **judge 가 부분 답변에 관대하다.** 0↔2 뒤집힘 0. 사람 메모 둘을 그대로 인용한다(요지 이탈 · 다중 의도 절반).
  - **③ 승격이 뜻하는 것** — `report_life` 격자의 `answered_mean` 을 지표로 읽는다. **잡음 띠는 이 30건의 불일치 4/30 을 기준으로**: 판정 차이 ±1 은 잡음, 0↔2 만 신호.
  - **④ 안 한 것** — 관대함 보정(루브릭 문구)을 **안 넣었다.** 넣으면 프롬프트 버전이 바뀌어 #277 의 84건과 축이 갈린다. 사람 결정으로 남긴다. `rag judge`(골든셋 33, OpenAI)와 합치지 않는다 (RAG-074).
  - **⑤ 한계** — 라벨이 30건이고 분포가 쏠려 있다(사람 2 가 24 · 1 이 6 · 0 이 0). 「못함」의 대부분은 judge 가 아니라 Life 상태(기권·거절)로 잡힌다 — 그것이 이 자의 성질이다. 라벨을 넓히려면 「못함」 쪽에서 뽑아야 한다.
  - **산출물** 목록.
- [ ] **Step 2: `roadmap.md`** — §3 D 의 `D15` 행을 ✅ 로(#348 · RAG-082, 수와 승격의 뜻 한 줄) · §4 「조건이 오면」 표에서 `D15` 행 제거 · §0 「지금 열린 것」 ③ 의 `D15` 언급 갱신 · 「닫힌 카드」 한 줄 · §7 에 09-09 #348 · §1 골든셋·평가 행에 judge 가 지표가 됐다는 한 줄 · §5 의 `~~LLM judge (RAG-007 원안)~~` 행 갱신. `grep -n "D15" docs/life/roadmap.md` 로 남은 언급을 다 본다.
- [ ] **Step 3: 전체 테스트** — `cd backend && uv run pytest -q -x --ignore=tests/place --ignore=tests/journey` (약 5분), 한 번.
- [ ] **Step 4: 커밋**

```bash
git add docs/life/decisions-rag.md docs/life/roadmap.md
git commit -m "docs: RAG-082 — 사람 라벨 30 대 judge 26/30, D15 동결 해제 (#348)"
```

---

## Self-Review

**Spec coverage** (PR #348 작업 목록 ↔ Task): 라벨 커밋 → Task 1 ✓ · 일치율을 파일로 → Task 2 ✓ · judge 를 지표로(로드맵·ADR 문구) → Task 3 ✓ · 관대함 보정 여부 → 안 넣고 ④ 에 기록 ✓ · RAG-08N → Task 3 ✓ · 로드맵 → Task 3 ✓

**Placeholder scan:** 없음. Task 3 의 수는 Task 2 산출물에서 온다.

**Type consistency:** `human_vs_judge(labels, judgments)` 의 반환 키는 Task 2 테스트가 쓰는 것과 같다. `agreement_rates` 의 반환 모양은 `judge.py:276-281` 그대로.
