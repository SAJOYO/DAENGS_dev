# 소스 확장 1 — nias 이동 두 장 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `nias-pet` 소스에 「함께 외출하기」·「함께 여행가기」 두 장을 더해 `category=travel` 코퍼스를 넓히고, **#343 이 세운 새 자(`life_v1_direct` 격자)로 전후를 대조한 첫 사례**를 만든다.

**Architecture:** 새 소스 모듈은 만들지 않는다 — 기존 `nias_pet.py` 의 `WANTED` 딕셔너리에 두 줄, 파서에 필요하면 탭 처리. 수집은 **임시 `DAENGS_DATA_DIR`** 에서 하고 새로 생긴 raw 두 쌍만 공유 데이터 디렉터리로 옮긴다(CLAUDE.md "개발 PC 에서 공유 `data/` 를 crawler 로 채우지 않는다"). 파싱·청킹·임베딩·적재는 **전체 코퍼스가 있는 공유 디렉터리**에서 돌린다.

**Tech Stack:** Python 3.12 · uv · BeautifulSoup · pgvector(집 서버 DB) · Gemini(전후 대조 수집·판정)

**Spec:** PR #343 본문 · `docs/life/decisions-rag.md` RAG-079 ③ · RAG-080 · `.superpowers/sdd/2026-09-08-src1/license-gate.md`

## Global Constraints

- 🔴 **`rag load` 를 `--prune` 과 함께 돌리지 않는다.** `stale()` 이 코퍼스 전체 기준이라(`rag/stages/load.py`) 부분 데이터로 돌리면 기존 9,838행이 지워진다. 이 카드는 **행을 더하기만** 하므로 prune 이 필요 없다.
- 🔴 **공유 `data/` 에서 `crawler run` 을 돌리지 않는다.** 임시 dir 에서 받고 새 파일만 복사한다. 공유 `manifests/crawl_log.jsonl` 은 건드리지 않는다(서버가 정본).
- **`guideline` 등급 소스는 이 카드에서 안 만든다** — 라이선스로 보류(`license-gate.md`). `crawler/sources/base.py` 의 "guideline 을 쓰는 소스가 아직 없다" 주석도 **그대로 둔다**.
- 공유 데이터 디렉터리: `C:/Users/403/Documents/workspace/DAENGS_dev/data` (`backend/.env` 의 `DAENGS_DATA_DIR`). 전체 코퍼스가 여기 있다(청크 10,631 · 임베딩 parquet 3종).
- 서빙 임베딩 모델 하나만 쓴다 — `rag embed` 를 `--all` 없이. `--model` 없으면 `config.settings.embedding_model_key`(`qwen3-embedding-0.6b`)다.
- 의존성 추가 금지. `crawler/` 에서 pymupdf 를 import 하지 않는다(`test_import_direction_packages.py`).
- 커밋 메시지 끝: `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` + `Claude-Session: https://claude.ai/code/session_014MevWGJ9PMs6mjWDZvwcis`. 제목에 `(#347)`.
- 테스트는 `cd backend && uv run pytest <파일> -q`. 전체는 마지막에 한 번.
- **push 는 컨트롤러가 한다.** 하위 에이전트는 커밋까지만.

---

## File Structure

| 파일 | 책임 | 상태 |
| --- | --- | --- |
| `backend/src/daengs_life/crawler/sources/registration/nias_pet.py` | `WANTED` 에 두 줄 | 수정 |
| `backend/src/daengs_life/rag/stages/parse/parsers/registration/nias_pet.py` | 새 두 장의 본문 경계 | 필요 시 수정 |
| `backend/tests/test_nias_travel.py` | 두 장의 Target·meta·파서 단위 테스트 | 생성 |
| `backend/tests/test_chunk.py` | `BY_SOURCE` 스냅샷의 `nias-pet` 줄 | 수정 |
| `data/README.md` (저장소 루트가 아니라 `DAENGS_DATA_DIR`) | subcategory 값 사전 | 수정 |
| `backend/src/daengs_life/rag/stages/goldenset.yaml` | `corpus.chunk_count`·`collected_on` | 수정 |
| `docs/life/{data-sources.md,decisions-rag.md,roadmap.md}` | 소스 표 · RAG-081 · 로드맵 | 수정 |
| `backend/evals/answer_quality/answers_life_v2_direct.jsonl` 외 | 전후 대조 산출물 | 생성 |

---

### Task 1: `WANTED` 두 줄과 실물 확인

**Files:**
- Modify: `backend/src/daengs_life/crawler/sources/registration/nias_pet.py` (`WANTED`, `:43-61`)
- Test: `backend/tests/test_nias_travel.py` (생성)

**Interfaces:**
- Produces: `WANTED["함께 외출하기"] = Page(<slug>, <subcategory>, "travel")` · `WANTED["함께 여행가기"] = Page(<slug>, <subcategory>, "travel")`. slug·subcategory 값은 Step 1 에서 정한다.

- [ ] **Step 1: 실물을 먼저 본다** (코드 수정 전, 읽기 전용)

```bash
cd backend && PYTHONIOENCODING=utf-8 uv run python - <<'EOF'
from daengs_life.crawler.core.fetch import Fetcher
from daengs_life.crawler.sources.registration.nias_pet import BASE, WANTED
from bs4 import BeautifulSoup
from urllib.parse import urljoin
f = Fetcher(); res = f.get(f"{BASE}/companion/index.do")
soup = BeautifulSoup(res.content, "lxml")
menus = {}
for a in soup.select('a[href*="new_petBoard.do"]'):
    menus.setdefault(a.get_text(" ", strip=True), urljoin(BASE, a["href"]))
for label in ("함께 외출하기", "함께 여행가기"):
    print("==", label, menus.get(label))
    r = f.get(menus[label]); s = BeautifulSoup(r.content, "lxml")
    box = s.select_one("section#contents")
    h2 = box.select_one("h2.pageTitle")
    print("  h2:", h2.get_text(" ", strip=True) if h2 else None)
    tabs = box.select(".tabscontents, .tabs, ul.tab li a")
    print("  tab-ish nodes:", len(tabs), [t.get_text(' ',strip=True)[:20] for t in tabs[:8]])
    txt = box.get_text(" ", strip=True)
    print("  chars:", len(txt), "| 반려동물", txt.count("반려동물"), "| 반려견", txt.count("반려견"), "| 제N조", __import__("re").findall(r"제\d+조", txt)[:5])
    print("  head:", txt[:300])
EOF
```

이 출력에서 셋을 정한다. 보고서에 그대로 적는다:
1. **탭이 있는가.** 있으면 파서가 잘라야 한다(Task 2). `nias_pet.py` 의 `extract()` 는 이미 `.tabscontents` 를 `decompose()` 한다 — 그것으로 충분한지 본다.
2. **`subcategory` 값.** 지금 `category=travel` 에 쓰인 값은 `transport-rail` · `transport-air` 둘뿐이다. 이 두 장은 운송약관이 아니라 **동반 외출·여행 안내**라 새 값이 맞다 — `travel-guide` 를 쓴다(둘 다 같은 값). 실물이 확연히 다르면 보고서에 근거를 적고 바꾼다.
3. **slug.** `outing` · `travel` (파일명이 `nias-pet-outing__YYYYMMDD.html` 이 된다). 기존 slug 와 겹치지 않는지 `WANTED` 를 보고 확인한다.

- [ ] **Step 2: 실패하는 테스트** — `backend/tests/test_nias_travel.py`

```python
"""nias-pet 이동 두 장 (#347). 네트워크 없이 Target·meta·파서만 잰다."""
from __future__ import annotations

from daengs_life.crawler.sources.registration.nias_pet import WANTED, NiasPet


def test_two_travel_pages_are_declared_with_the_travel_category() -> None:
    assert "함께 외출하기" in WANTED and "함께 여행가기" in WANTED
    outing, travel = WANTED["함께 외출하기"], WANTED["함께 여행가기"]
    assert outing.category == "travel" and travel.category == "travel"
    assert outing.subcategory == travel.subcategory == "travel-guide"
    assert {outing.slug, travel.slug}.isdisjoint({p.slug for k, p in WANTED.items() if k not in ("함께 외출하기", "함께 여행가기")})


def test_every_declared_category_is_one_the_db_accepts() -> None:
    # documents.category 의 CHECK (db/init/01_schema.sql · 2026-09-06 마이그레이션)
    allowed = {"policy", "travel", "food", "insurance"}
    assert {p.category for p in WANTED.values()} <= allowed


def test_slugs_are_unique() -> None:
    slugs = [p.slug for p in WANTED.values()]
    assert len(slugs) == len(set(slugs))


def test_source_class_defaults_are_unchanged() -> None:
    # 두 장은 Target.meta 로 category·subcategory 를 덮어쓴다. 클래스 기본값은 그대로여야
    # 나머지 열 장이 안 흔들린다 (store.py 의 meta 우선 규칙).
    assert NiasPet.category == "policy" and NiasPet.subcategory == "pet-life-guide"
    assert NiasPet.trust_level == "official"
```

- [ ] **Step 3: 실패 확인** — `cd backend && uv run pytest tests/test_nias_travel.py -q` → FAIL (`"함께 외출하기" in WANTED`).

- [ ] **Step 4: `WANTED` 에 두 줄.** 기존 항목은 순서까지 그대로 두고, 음식 블록 아래에 주석과 함께 더한다:

```python
    # --- 동반 이동 (2026-09-08, #347 / F0-2 정찰) ---
    # 2026-08-27 정찰이 "travel 도메인이라 운송약관 카드와 같이 간다" 며 미뤄 둔 두 장이다.
    # 운송약관(코레일 · SRT · 지하철 · 항공)은 규정 본문이고 이쪽은 **동반 외출·여행 안내**라
    # subcategory 를 나눈다 — 값 사전은 `data/README.md`.
    "함께 외출하기": Page("outing", "travel-guide", "travel"),
    "함께 여행가기": Page("travel", "travel-guide", "travel"),
```

- [ ] **Step 5: 통과 확인** — `uv run pytest tests/test_nias_travel.py -q` PASS. `uv run ruff check src/daengs_life/crawler/sources/registration/nias_pet.py tests/test_nias_travel.py`.

- [ ] **Step 6: 커밋**

```bash
git add backend/src/daengs_life/crawler/sources/registration/nias_pet.py backend/tests/test_nias_travel.py
git commit -m "feat: nias-pet 에 동반 이동 두 장 — 함께 외출하기 · 함께 여행가기 (#347)"
```

---

### Task 2: 수집(임시 dir) → 공유 dir 로 raw 두 쌍

**Files:**
- Create (데이터, git 밖): 공유 `data/raw/registration/nias-pet-outing__YYYYMMDD.{html,meta.json}` · `nias-pet-travel__…`
- Modify: `backend/src/daengs_life/rag/stages/parse/parsers/registration/nias_pet.py` (필요할 때만)

**Interfaces:**
- Consumes: Task 1 의 `WANTED`.
- Produces: 공유 데이터 디렉터리의 raw 두 쌍. Task 3 이 파싱한다.

- [ ] **Step 1: 임시 dir 에서 수집**

```bash
cd backend
export DAENGS_DATA_DIR=/tmp/daengs-src1
mkdir -p "$DAENGS_DATA_DIR/manifests"
cp "C:/Users/403/Documents/workspace/DAENGS_dev/data/manifests/seed_sources.yaml" "$DAENGS_DATA_DIR/manifests/"
: > "$DAENGS_DATA_DIR/manifests/crawl_log.jsonl"     # 로그 없이 돌면 전 소스가 due 로 잡힌다 (RAG-050)
PYTHONIOENCODING=utf-8 uv run python -m daengs_life.crawler run --source nias-pet -v
ls -la "$DAENGS_DATA_DIR/raw/registration/"
```

Expected: 12개 페이지가 받아진다(임시 dir 이라 전부 새 파일). `nias-pet-outing__*.html` · `nias-pet-travel__*.html` 이 있어야 한다. **없으면 STOP** — 메뉴 제목이 안 맞은 것이니 Step 1 의 실물 출력과 대조해 라벨을 고치고 다시 돈다.

- [ ] **Step 2: 새 두 쌍만 공유 dir 로 복사**

```bash
S=/tmp/daengs-src1/raw/registration
D="C:/Users/403/Documents/workspace/DAENGS_dev/data/raw/registration"
ls "$D" | grep -E "nias-pet-(outing|travel)__" && echo "이미 있다 — 덮어쓰기 전에 보고" 
cp "$S"/nias-pet-outing__*.html "$S"/nias-pet-outing__*.meta.json "$D"/
cp "$S"/nias-pet-travel__*.html "$S"/nias-pet-travel__*.meta.json "$D"/
ls -la "$D" | grep -E "outing|travel__"
```

🔴 나머지 열 장은 **복사하지 않는다.** 공유 dir 의 것은 2026-08-27·09-06 자 정본이고, 오늘 자로 덮으면 이 카드가 안 건드리기로 한 열 장의 본문이 같이 바뀐다.

- [ ] **Step 3: 파서가 두 장을 제대로 자르는지 dry-run**

```bash
cd backend && unset DAENGS_DATA_DIR
PYTHONIOENCODING=utf-8 uv run python -m daengs_life.rag parse --source nias-pet --dry-run -v 2>&1 | tail -30
```

Expected: 12개 문서. 새 두 장의 제목·글자 수가 보인다. **탭 잔재(다른 주제의 본문)가 섞여 있으면** `rag/stages/parse/parsers/registration/nias_pet.py` 를 고친다 — 음식 두 장이 탭을 자르는 방식을 그대로 따르고, 무엇을 왜 잘랐는지 주석에 적는다. 안 섞였으면 파서는 **안 고친다**.

- [ ] **Step 4: 커밋** (파서를 고쳤을 때만)

```bash
git add backend/src/daengs_life/rag/stages/parse/parsers/registration/nias_pet.py
git commit -m "fix: nias 이동 두 장의 본문 경계 — 탭 잔재를 자른다 (#347)"
```
안 고쳤으면 커밋 없이 보고서에 "파서 무수정" 이라고 적는다.

---

### Task 3: 파싱 → 청킹 → 임베딩 → 적재

**Files:**
- Modify: `backend/tests/test_chunk.py` (`BY_SOURCE` 의 `nias-pet` 줄)
- Modify: `backend/src/daengs_life/rag/stages/goldenset.yaml` (`corpus.chunk_count` · `collected_on`)

**Interfaces:**
- Consumes: Task 2 의 raw.
- Produces: 집 서버 `documents` 에 새 행. Task 4 가 그 위에서 답을 모은다.

- [ ] **Step 1: 파싱·청킹 (해당 소스만)**

```bash
cd backend
PYTHONIOENCODING=utf-8 uv run python -m daengs_life.rag parse --source nias-pet -v 2>&1 | tail -5
PYTHONIOENCODING=utf-8 uv run python -m daengs_life.rag chunk --source nias-pet -v 2>&1 | tail -5
```
전후 청크 수를 적는다(전체 10,631 에서 얼마가 됐나, nias-pet 이 몇에서 몇으로).

- [ ] **Step 2: `BY_SOURCE` 스냅샷 갱신** — `tests/test_chunk.py` 의 `nias-pet` 줄만 새 수로. `DOC_COUNT`·`TOTAL` 은 파생값이라 안 건드린다.

Run: `uv run pytest tests/test_chunk.py -q` → PASS.

- [ ] **Step 3: 임베딩 (증분)**

```bash
PYTHONIOENCODING=utf-8 uv run python -m daengs_life.rag embed 2>&1 | tail -12
```
Expected: 재사용 대부분 + 새 청크만 인코딩. **재사용/신규 수를 적는다.** 전량 재인코딩(수천 건)이 뜨면 **STOP** — parquet schema/model 불일치이니 원인을 보고한다(A1 · RAG-064).

- [ ] **Step 4: 적재 — `--prune` 없이, dry-run 먼저**

```bash
PYTHONIOENCODING=utf-8 uv run python -m daengs_life.rag load --dry-run --show 5 2>&1 | tail -20
```
Expected: 새 청크만 삽입 대상. **삭제 대상이 보이면 STOP** (이 카드는 아무것도 안 지운다).

```bash
PYTHONIOENCODING=utf-8 uv run python -m daengs_life.rag load 2>&1 | tail -10
```

- [ ] **Step 5: DB 확인**

```bash
PYTHONIOENCODING=utf-8 uv run python - <<'EOF'
import asyncio, asyncpg
from daengs_backend.config import settings as s
async def main():
    c = await asyncpg.connect(host=s.db_host, port=s.db_port, user=s.db_user,
                              password=s.db_password.get_secret_value(), database=s.db_name)
    print("documents", await c.fetchval("select count(*) from documents"))
    for r in await c.fetch("select category, count(*) from documents group by 1 order by 2 desc"): print(" ", dict(r))
    for r in await c.fetch("select metadata->>'subcategory' sc, count(*) from documents where category='travel' group by 1 order by 2 desc"): print("  travel:", dict(r))
    await c.close()
asyncio.run(main())
EOF
```
Expected: `documents` 가 9,838 에서 늘고 `travel` 에 `travel-guide` 가 생긴다. **줄었으면 STOP 하고 즉시 보고.**

- [ ] **Step 6: `goldenset.yaml` 의 코퍼스 수와 날짜 갱신** → `uv run pytest tests/test_goldenset.py -q`

- [ ] **Step 7: 커밋**

```bash
git add backend/tests/test_chunk.py backend/src/daengs_life/rag/stages/goldenset.yaml
git commit -m "chore: nias 이동 두 장 적재 — 청크 스냅샷·골든셋 코퍼스 수 갱신 (#347)"
```

---

### Task 4: 새 자로 전후 대조

**Files:**
- Create: `backend/evals/answer_quality/answers_life_v2_direct.jsonl` · `judgments_life_v2_direct.jsonl` · `report_life_v2_direct.md` · `summary_life_v2_direct.json`

**Interfaces:**
- Consumes: Task 3 의 DB, `questions_life_v1.jsonl`(동결, 그대로).
- Produces: `report_life_v2_direct.md` — `report_life_v1_direct.md` 와 칸 단위로 대조한다.

- [ ] **Step 1: 프롬프트 버전을 적어 둔다** — `grep -n "^VERSION" src/daengs_life/rag/stages/generate.py`.
  기준선은 VERSION 3 에서 찍었고 지금은 **4**(#337 이 올렸다). ⚠ **이 카드의 대조에는 코퍼스 변화와 프롬프트 변화가 섞인다.** 섞인 채로 두고 리포트·기록에 그대로 적는다 — 프롬프트만 바뀐 기준선을 다시 찍는 것은 별도 카드다(RAG-081 에 남긴다).

- [ ] **Step 2: 수집 (Life 직접 축, 140문항)**

```bash
cd backend
PYTHONIOENCODING=utf-8 uv run python -m daengs_evals.answer_quality.collect_life \
  --questions evals/answer_quality/questions_life_v1.jsonl --label life_v2_direct
```
**한 프로세스만.** 10~20분. 백그라운드로 돌리면 로그를 `/tmp/collect_life_v2.log` 에 두고 프로세스 종료까지 폴링한다. 끝나면 상태 분포를 적는다.

- [ ] **Step 3: 판정**

```bash
PYTHONIOENCODING=utf-8 uv run python -m daengs_evals.answer_quality.judge score \
  --answers evals/answer_quality/answers_life_v2_direct.jsonl \
  --questions evals/answer_quality/questions_life_v1.jsonl \
  --out evals/answer_quality/judgments_life_v2_direct.jsonl
```

- [ ] **Step 4: 리포트**

```bash
PYTHONIOENCODING=utf-8 uv run python -m daengs_evals.answer_quality.report_life report \
  --answers evals/answer_quality/answers_life_v2_direct.jsonl \
  --judgments evals/answer_quality/judgments_life_v2_direct.jsonl \
  --label life_v2_direct --meta-documents <Task3 Step5 의 수> --meta-db-host 192.168.0.22 \
  --meta-prompt-version <Step1 의 수> \
  --note "소스 확장 1 (#347) — nias 이동 두 장. 기준선 life_v1_direct 는 VERSION 3, 이쪽은 VERSION 4 라 코퍼스 변화와 프롬프트 변화가 섞여 있다"
```

- [ ] **Step 5: 칸 단위 대조** — 스크립트로 `summary_life_v1_direct.json` 과 `summary_life_v2_direct.json` 의 `grid` 를 대조해 **움직인 칸만** 낸다. 주제별 `못함`·`오거절` 변화와, `life_travel` 칸이 어떻게 움직였는지를 표로 보고서에 적는다. 총계 한 줄로 말하지 않는다(RAG-070 ④).

- [ ] **Step 6: 커밋**

```bash
git add backend/evals/answer_quality/answers_life_v2_direct.jsonl backend/evals/answer_quality/judgments_life_v2_direct.jsonl backend/evals/answer_quality/report_life_v2_direct.md backend/evals/answer_quality/summary_life_v2_direct.json
git commit -m "chore: life_v2_direct — nias 이동 두 장 뒤의 격자 (#347)"
```

---

### Task 5: 골든셋 회귀 게이트 (lap32)

- [ ] **Step 1: 랩 한 번**

```bash
cd backend && PYTHONIOENCODING=utf-8 uv run python -m daengs_life.rag generate --lap lap32 2>&1 | tail -10
```
정확한 플래그는 `uv run python -m daengs_life.rag generate --help` 로 확인한다(`--lap` 이름이 다를 수 있다). 33문항이라 몇 분이다.

- [ ] **Step 2: 대조**

```bash
PYTHONIOENCODING=utf-8 uv run python -m daengs_life.rag score-laps --against lap31 2>&1 | tail -30
```
Expected: **잃은 문항 0.** 잃은 것이 있으면 문항 이름과 원인을 보고서에 적는다(코퍼스가 늘어 순위가 밀렸을 수 있다 — 그것도 결과다).

- [ ] **Step 3: 커밋** — 랩 파일은 `DAENGS_DATA_DIR` 안이라 git 밖이다. 커밋할 것이 없으면 보고서에만 적는다. `score-laps` 표를 보고서에 붙인다.

---

### Task 6: 문서

**Files:** `docs/life/data-sources.md` · `docs/life/decisions-rag.md` · `docs/life/roadmap.md` · `DAENGS_DATA_DIR/README.md`

- [ ] **Step 1: `data/README.md`(데이터 디렉터리)** 의 subcategory 값 사전에 `travel-guide` 한 줄. 이 파일은 git 밖이라 커밋 대상이 아니다 — 고친 사실만 보고서에 적는다.

- [ ] **Step 2: `docs/life/data-sources.md`** — §5 동반 이동 절에 nias 두 장을 더하고 §0 진행 현황 표의 수를 맞춘다. §10 "2026-09 정찰 2" 결론의 이동 항목에 **✅ 받았다** 를 표시한다.

- [ ] **Step 3: `docs/life/decisions-rag.md` 끝에 RAG-081** — `---` 뒤에. 제목: `## RAG-081. 소스 확장 1 — nias 이동 두 장, 그리고 guideline 은 라이선스로 보류 — ✅ 확정 (2026-09-0N)`. 절:
  - **배경** — RAG-079 ③④ 와 `F0-2`(#343)가 고른 것 둘 중 하나가 게이트에서 떨어졌다.
  - **① `guideline` 첫 소스는 보류** — `.superpowers/sdd/2026-09-08-src1/license-gate.md` 의 실측을 옮긴다(이용조건 표기 0 · 카라 사이트 all rights reserved · 발행 주체가 서울시가 아님 · 내용은 맞았고 증상 어휘 0). 선례는 RAG-048 ①. **푸는 길**(카라에 문의 / 서울시 발행본 / `animal.go.kr` 은 robots 로 불가)을 적는다.
  - **② 받은 것** — 두 장의 청크 수 · `subcategory=travel-guide` 신설 이유 · 적재 뒤 `documents` 수.
  - **③ 전후 대조** — `report_life_v2_direct.md` 의 주제 합계와 움직인 칸. ⚠ **VERSION 3 → 4 가 섞였다**는 것을 눈에 띄게.
  - **④ 회귀** — lap32 대 lap31, 잃은 문항.
  - **⑤ 바꾸려면** — 다음 소스 카드가 알아야 할 것(공유 `data/` 수집 규칙, `--prune` 금지).
  - **산출물** 목록.
- [ ] **Step 4: `docs/life/roadmap.md`** — §1 코퍼스 분포 행(새 수) · §1 골든셋·평가 행에 lap32 · §3 F 의 관련 행 · §4 2번 「소스 확장 카드들」 행에 **첫 장이 닫혔고 `guideline` 은 원천부터**임을 한 줄 · §7 에 09-0N #347 한 줄. `grep -n "§4 의" docs/life/roadmap.md` 로 번호 인용을 확인한다(이 카드는 §4 의 번호를 바꾸지 않는다 — 「소스 확장 카드들」은 계속 열려 있다).
- [ ] **Step 5: 전체 테스트** — `cd backend && uv run pytest -q -x --ignore=tests/place --ignore=tests/journey` (약 6분).
- [ ] **Step 6: 커밋**

```bash
git add docs/life/data-sources.md docs/life/decisions-rag.md docs/life/roadmap.md
git commit -m "docs: RAG-081 — nias 이동 두 장, guideline 은 라이선스로 보류 (#347)"
```

---

## Self-Review

**Spec coverage** (PR #347 작업 목록 ↔ Task): 정찰 마무리 → 착수 때 컨트롤러가 끝냄(보류) ✓ · 소스 모듈 → 🚫 보류로 없음 ✓ · nias `WANTED` → Task 1 ✓ · 시드/테스트 → Task 1(시드는 기존 소스라 불필요, Explore 확인) ✓ · 수집~적재 → Task 2·3 ✓ · 전후 대조 → Task 4 ✓ · 회귀 게이트 → Task 5 ✓ · 문서 셋 → Task 6 ✓

**Placeholder scan:** Task 4 Step 4 의 `<…>` 는 앞 단계 실측값을 넣는 자리이고 어디서 오는지 적혀 있다. 그 외 없음.

**Type consistency:** `Page(slug, subcategory, category)` 의 인자 순서는 `nias_pet.py` 의 `NamedTuple` 정의와 같다(`slug`, `subcategory`, `category="policy"`). 테스트가 쓰는 `WANTED`·`NiasPet` 는 그 모듈의 실제 이름이다.
