# GCP 코퍼스 파이프라인 (#325) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 크롤 → 파싱 → 청킹 → 임베딩 → 가드 → 적재를 한 번에 도는 `corpus-refresh` 진입점과 그것을 Cloud Run Job 으로 돌리는 이미지·gcloud 스크립트·문서를 만든다. 완료 기준은 "Cloud Scheduler 가 매일 04:00 KST 에 잡을 돌린다".

**Architecture:** `daengs_life/jobs/` 에 얇은 조립층을 두고 기존 `crawler.run` 과 `rag/__main__.py` 의 `cmd_*` 를 순서대로 부른다. 적재만 `cmd_load` 를 안 쓰고 `jobs/load.py` 가 `stages.load` 의 함수들을 가드와 함께 한 트랜잭션으로 조립한다. 코퍼스는 GCS 버킷을 `/data` 로 마운트해 경로 코드를 안 고친다. DB 는 VM 의 pgvector 를 VPC 내부로 본다.

**Tech Stack:** Python 3.12 · uv · psycopg3 · sentence-transformers(Qwen3-Embedding-0.6B) · Docker · Cloud Run Jobs · Cloud Scheduler · Cloud Storage · Artifact Registry · Secret Manager · `google-cloud-run`(동시 실행 확인)

**Spec:** `docs/deploy/corpus-pipeline.md`

## Global Constraints

- Python 은 `>=3.12,<3.13`. 실행은 항상 `uv run` 을 거친다 (`backend/` 에서).
- 의존성은 **반드시 `uv add`** 로. `pyproject.toml` 을 직접 고치지 않는다. `uv.lock` 커밋.
- lock 의 torch 는 리눅스에서 CPU 인덱스다. CUDA 이미지는 Dockerfile 에서 덮어쓰고 lock 은 안 건드린다.
- `crawler` 패키지는 `app/`·`tasks/`·`rag/` 를 import 하지 않는다. `jobs` 가 `crawler` 를 부르는 범위는 `tests/test_import_direction_packages.py` 의 `ALLOWED` 에 적는다.
- 집 서버의 compose 서비스·코퍼스·runbook §6 "Life 코퍼스만 동기화" 절차는 건드리지 않는다.
- GCP DB 의 `documents` 는 파이프라인만 쓴다. `POSTGRES_USER` 는 `daengs`.
- 비밀번호·API 키는 diff 에 없어야 한다. Secret Manager 로 주입.
- 커밋 메시지는 한국어, 끝에 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` 와 `Claude-Session:` 줄.
- 테스트는 `backend/tests/` 평면 구조. `uv run pytest tests/test_jobs_*.py` 로 새 테스트만 돌리고, 마지막에 전체.
- 브랜치는 `chore/gcp-crawler-corpus-cutover` (#325). 새로 파지 않는다.

---

## 파일 구조

| 경로 | 책임 |
| --- | --- |
| `backend/src/daengs_life/jobs/__init__.py` | 패키지 설명. "Celery 없이 한 프로세스에서 끝까지 도는 배치 진입점" |
| `backend/src/daengs_life/jobs/stages.py` | 단계 이름 상수 · `--stages` 파싱 · 각 단계를 부르는 함수(`run_crawl` `run_parse` `run_chunk` `run_embed` `run_load`) |
| `backend/src/daengs_life/jobs/guard.py` | 순수 함수 `check(before, planned, losing, *, max_drop)` → `Verdict` |
| `backend/src/daengs_life/jobs/load.py` | `stages.load` 조립: prepare → count → metadata_loss → guard → 한 트랜잭션 upsert+prune |
| `backend/src/daengs_life/jobs/lock.py` | Cloud Run API 로 같은 잡의 다른 실행 확인. 로컬이면 no-op |
| `backend/src/daengs_life/jobs/corpus_refresh.py` | CLI `main()`. 인자 → 단계 순서대로 실행 → 종료 코드 |
| `backend/tests/test_jobs_guard.py` | 가드 판정 |
| `backend/tests/test_jobs_stages.py` | `--stages` 파싱 · 조립 순서 (각 단계를 가짜로) |
| `backend/tests/test_jobs_lock.py` | 실행 목록을 가짜로 주고 건너뛰기 판정 |
| `backend/tests/test_import_direction_packages.py` | `ALLOWED["jobs"]` 추가 |
| `docker/pipeline/Dockerfile` | CPU/CUDA 빌드 인자. ml 그룹 + 가중치 굽기 |
| `docker/pipeline/entrypoint.sh` | 시드 파일을 `/data/manifests/` 로 복사한 뒤 `corpus-refresh "$@"` |
| `infra/gcp/pipeline.sh` · `pipeline-teardown.sh` · `README.md` | gcloud 명령 순서 · 삭제 · 사람이 할 것 |
| `docs/decisions.md` · `docs/deploy/{runbook,roadmap}.md` · `docs/life/roadmap.md` · `docker-compose.gcp.yml` · `CLAUDE.md` · `README.md` | 문서 |

---

### Task 1: 가드 (순수 판정)

**Files:**
- Create: `backend/src/daengs_life/jobs/__init__.py`
- Create: `backend/src/daengs_life/jobs/guard.py`
- Test: `backend/tests/test_jobs_guard.py`

**Interfaces:**
- Produces: `guard.check(before: int, planned: int, losing: list[tuple[str, int, int]], *, max_drop: float = 0.2) -> Verdict` — `Verdict(ok: bool, reasons: list[str])`. `before` 는 지금 `documents` 행 수, `planned` 는 적재 뒤 남을 행 수(= prepare 가 만든 행 수), `losing` 은 `stages.load.metadata_loss` 반환값.

- [x] **Step 1: 패키지 파일과 실패하는 테스트**

`backend/src/daengs_life/jobs/__init__.py`:

```python
"""Celery 없이 **한 프로세스에서 끝까지** 도는 배치 진입점 (D-062).

`tasks/` 는 Celery 워커가 받는 태스크이고 크롤에서 멈춘다. 여기는 Cloud Run Job 이 부르며
crawl → parse → chunk → embed → guard → load 를 순서대로 지난다. 단계 로직은 `crawler` 와
`rag` 에 있고 여기는 **조립과 가드**만 둔다 — 단계 하나를 고칠 일이 생기면 그쪽을 고친다.

의존 방향: `jobs → crawler.{run, core.cadence, core.registry}` · `jobs → rag` · `jobs → tasks.crawl_runs`
(psycopg 만 쓰는 기록 모듈이라 Celery 를 끌고 오지 않는다). 범위는
`tests/test_import_direction_packages.py` 가 막는다.
"""
```

`backend/tests/test_jobs_guard.py`:

```python
"""적재 가드 (D-062). DB·파일 없이 숫자만 넣고 판정을 본다."""
from daengs_life.jobs import guard


def test_passes_when_rows_grow():
    v = guard.check(before=9_000, planned=9_800, losing=[])
    assert v.ok and v.reasons == []


def test_passes_on_first_load_when_table_is_empty():
    v = guard.check(before=0, planned=9_800, losing=[])
    assert v.ok


def test_blocks_when_rows_drop_more_than_threshold():
    v = guard.check(before=10_000, planned=7_000, losing=[])
    assert not v.ok
    assert any("30%" in r for r in v.reasons)


def test_allows_drop_within_threshold():
    v = guard.check(before=10_000, planned=8_500, losing=[])
    assert v.ok


def test_threshold_is_configurable():
    assert not guard.check(before=100, planned=95, losing=[], max_drop=0.01).ok
    assert guard.check(before=100, planned=95, losing=[], max_drop=0.10).ok


def test_blocks_when_metadata_key_would_vanish():
    v = guard.check(before=100, planned=100, losing=[("org", 2_592, 0)])
    assert not v.ok
    assert any("org" in r for r in v.reasons)


def test_reports_every_reason_not_just_the_first():
    v = guard.check(before=100, planned=10, losing=[("org", 50, 0)])
    assert len(v.reasons) == 2
```

- [x] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_jobs_guard.py -q`
Expected: `ModuleNotFoundError: No module named 'daengs_life.jobs.guard'`

- [x] **Step 3: 구현**

`backend/src/daengs_life/jobs/guard.py`:

```python
"""적재 가드 — 사람 승인 대신 기계가 보는 셋 (D-062, `docs/deploy/corpus-pipeline.md` §3).

① 행 수 급감: 적재 뒤 남을 행이 지금보다 `max_drop` 비율 이상 적으면 막는다. 코퍼스 절반이
   파싱에서 조용히 빠졌을 때 stale prune 이 그 절반을 지우는 사고를 막는 자리다.
② 파서 예외는 여기 없다 — `rag parse` 가 예외 1건이면 종료 코드 1 이라 `stages.run_parse` 가 멈춘다.
③ 메타데이터 손실: `stages.load.metadata_loss` 가 잡은 키(RAG-066 ①). 예전에 `org` 2,592행이
   그렇게 지워졌고 아무 에러도 안 났다.

**순수 함수다.** DB 도 파일도 안 본다 — 그래서 숫자만 넣고 테스트한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Verdict:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def check(before: int, planned: int, losing: list[tuple[str, int, int]], *,
          max_drop: float = 0.2) -> Verdict:
    """`before` 지금 행 수 · `planned` 적재 뒤 행 수 · `losing` = metadata_loss() 반환값."""
    reasons: list[str] = []

    if before > 0 and planned < before:
        drop = (before - planned) / before
        if drop >= max_drop:
            reasons.append(f"행 수가 {before:,} → {planned:,} 로 {drop:.0%} 줄어든다"
                           f" (한계 {max_drop:.0%})")

    for key, in_db, incoming in losing:
        reasons.append(f"메타 키 {key!r} 가 사라진다 (DB {in_db:,}행 → 이번 {incoming}행)")

    return Verdict(ok=not reasons, reasons=reasons)


__all__ = ["Verdict", "check"]
```

- [x] **Step 4: 통과 확인**

Run: `cd backend && uv run pytest tests/test_jobs_guard.py -q`
Expected: `7 passed`

- [x] **Step 5: 커밋**

```bash
git add backend/src/daengs_life/jobs/__init__.py backend/src/daengs_life/jobs/guard.py backend/tests/test_jobs_guard.py
git commit -m "feat(jobs): 적재 가드 — 행 수 급감·메타 키 손실을 숫자만으로 판정 (D-062)"
```

---

### Task 2: 단계 실행 함수와 `--stages` 파싱

**Files:**
- Create: `backend/src/daengs_life/jobs/stages.py`
- Test: `backend/tests/test_jobs_stages.py`

**Interfaces:**
- Consumes: `daengs_life.crawler.run.run(source_id) -> RunResult`(`.unavailable` `.fetched` `.changed` `.failed` `.skipped` `.run_id` `.changed_slugs`), `crawler.core.cadence.due_sources(seeds, implemented=, now=)`, `crawler.core.registry.load_seeds() / resolve(seed)`, `daengs_life.tasks.crawl_runs.start(source_id, trigger) / finish(row_id, status, run_id=, counts=, changed_slugs=, error=)`, `daengs_life.rag.__main__.cmd_parse / cmd_chunk / cmd_embed(argparse.Namespace) -> int`.
- Produces: `ORDER = ("crawl", "parse", "chunk", "embed", "load")`, `parse_stages(text: str | None) -> list[str]`, `run_crawl(source_ids, *, dry_run) -> CrawlSummary`, `run_parse(*, dry_run) -> int`, `run_chunk(*, dry_run) -> int`, `run_embed(*, full, dry_run) -> int`. `CrawlSummary(selected: list[str], ok: int, failed: int, unavailable: int)`.

- [x] **Step 1: 실패하는 테스트**

`backend/tests/test_jobs_stages.py`:

```python
"""조립층 — 단계 순서·인자 파싱·크롤 요약. 실제 크롤·파싱은 가짜로 바꾼다."""
import argparse
from types import SimpleNamespace

import pytest

from daengs_life.jobs import stages


def test_default_is_every_stage_in_order():
    assert stages.parse_stages(None) == ["crawl", "parse", "chunk", "embed", "load"]


def test_subset_keeps_pipeline_order_regardless_of_input_order():
    assert stages.parse_stages("load,parse") == ["parse", "load"]


def test_unknown_stage_is_an_error():
    with pytest.raises(ValueError, match="모르는 단계"):
        stages.parse_stages("crawl,index")


def test_run_parse_passes_dry_run_to_the_cli_function(monkeypatch):
    seen = {}

    def fake_cmd_parse(args: argparse.Namespace) -> int:
        seen.update(vars(args))
        return 0

    monkeypatch.setattr(stages.rag_cli, "cmd_parse", fake_cmd_parse)
    assert stages.run_parse(dry_run=True) == 0
    assert seen == {"source": None, "limit": 0, "force": False, "dry_run": True, "verbose": False}


def test_run_embed_uses_serving_model_and_full_flag(monkeypatch):
    seen = {}
    monkeypatch.setattr(stages.rag_cli, "cmd_embed", lambda a: seen.update(vars(a)) or 0)
    stages.run_embed(full=True, dry_run=False)
    assert seen["model"] == stages.rag_config.settings.embedding_model_key
    assert seen["full"] is True and seen["all_models"] is False


def test_run_crawl_picks_due_sources_and_records_each(monkeypatch):
    monkeypatch.setattr(stages.registry, "load_seeds", lambda: {"a": {}, "b": {}, "c": {}})
    monkeypatch.setattr(stages.registry, "resolve", lambda seed: object())
    monkeypatch.setattr(stages.cadence, "due_sources", lambda seeds, implemented, now: ["a", "c"])
    started, finished = [], []
    monkeypatch.setattr(stages.crawl_runs, "start", lambda sid, trig: started.append((sid, trig)) or 1)
    monkeypatch.setattr(stages.crawl_runs, "finish", lambda rid, status, **kw: finished.append(status))
    results = {
        "a": SimpleNamespace(unavailable=None, fetched=3, changed=1, failed=0, skipped=2,
                             run_id="r1", changed_slugs=["x"]),
        "c": SimpleNamespace(unavailable="LAW_OC 미설정", fetched=0, changed=0, failed=0,
                             skipped=0, run_id=None, changed_slugs=[]),
    }
    monkeypatch.setattr(stages.crawler_run, "run", lambda sid, **kw: results[sid])

    s = stages.run_crawl(None, dry_run=False)

    assert s.selected == ["a", "c"]
    assert (s.ok, s.failed, s.unavailable) == (1, 0, 1)
    assert started == [("a", "due"), ("c", "due")]
    assert finished == ["ok", "unavailable"]


def test_run_crawl_manual_sources_skip_due_check(monkeypatch):
    monkeypatch.setattr(stages.registry, "load_seeds", lambda: {"a": {}})
    monkeypatch.setattr(stages.cadence, "due_sources",
                        lambda *a, **k: pytest.fail("수동 지정이면 due 판정을 안 탄다"))
    monkeypatch.setattr(stages.crawl_runs, "start", lambda sid, trig: 1)
    monkeypatch.setattr(stages.crawl_runs, "finish", lambda *a, **k: None)
    monkeypatch.setattr(stages.crawler_run, "run",
                        lambda sid, **kw: SimpleNamespace(unavailable=None, fetched=1, changed=0,
                                                          failed=0, skipped=0, run_id="r",
                                                          changed_slugs=[]))
    s = stages.run_crawl(["a"], dry_run=False)
    assert s.selected == ["a"] and s.ok == 1


def test_run_crawl_exception_in_one_source_is_recorded_and_others_continue(monkeypatch):
    monkeypatch.setattr(stages.registry, "load_seeds", lambda: {"a": {}, "b": {}})
    monkeypatch.setattr(stages.crawl_runs, "start", lambda sid, trig: 1)
    finished = []
    monkeypatch.setattr(stages.crawl_runs, "finish",
                        lambda rid, status, **kw: finished.append((status, kw.get("error"))))

    def boom_or_ok(sid, **kw):
        if sid == "a":
            raise RuntimeError("네트워크")
        return SimpleNamespace(unavailable=None, fetched=1, changed=0, failed=0, skipped=0,
                               run_id="r", changed_slugs=[])

    monkeypatch.setattr(stages.crawler_run, "run", boom_or_ok)
    s = stages.run_crawl(["a", "b"], dry_run=False)
    assert (s.ok, s.failed) == (1, 1)
    assert finished[0][0] == "failed" and "RuntimeError" in finished[0][1]
```

- [x] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_jobs_stages.py -q`
Expected: `ModuleNotFoundError: No module named 'daengs_life.jobs.stages'`

- [x] **Step 3: 구현**

`backend/src/daengs_life/jobs/stages.py`:

```python
"""단계 하나씩을 부르는 함수들. **로직은 여기 없다** — `crawler.run` 과 `rag/__main__.py` 의
`cmd_*` 를 그대로 부른다 (D-062). `cmd_*` 가 argparse Namespace 를 받으므로 여기서 같은 모양을
만들어 준다. 그 함수의 인자가 늘면 여기 Namespace 도 같이 는다 — 어긋나면 AttributeError 로
바로 터지지 조용히 다른 값을 쓰지 않는다.

크롤은 `tasks/crawl.py` 의 `crawl_source` 와 같은 흐름이되 Celery 재시도가 없다. 소스 하나가
죽어도 다음 소스로 가고, 시도마다 `crawl_runs` 에 한 행을 남기는 것은 그쪽과 같다 (RAG-047).
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from datetime import datetime

from daengs_life.crawler import run as crawler_run
from daengs_life.crawler.core import cadence, registry
from daengs_life.crawler.core.config import KST
from daengs_life.rag import __main__ as rag_cli
from daengs_life.rag.core import config as rag_config
from daengs_life.tasks import crawl_runs

log = logging.getLogger(__name__)

#: 파이프라인 순서. `rag/pipeline.py` 의 STAGES 와 같은 뜻이지만 여기는 잡이 도는 단위만 있다.
ORDER: tuple[str, ...] = ("crawl", "parse", "chunk", "embed", "load")


def parse_stages(text: str | None) -> list[str]:
    """`--stages parse,load` → 파이프라인 순서로 정렬된 목록. 없으면 전부."""
    if not text:
        return list(ORDER)
    wanted = {s.strip() for s in text.split(",") if s.strip()}
    unknown = wanted - set(ORDER)
    if unknown:
        raise ValueError(f"모르는 단계: {sorted(unknown)}  가능: {list(ORDER)}")
    return [s for s in ORDER if s in wanted]


@dataclass
class CrawlSummary:
    selected: list[str] = field(default_factory=list)
    ok: int = 0
    failed: int = 0
    unavailable: int = 0


def run_crawl(source_ids: list[str] | None, *, dry_run: bool) -> CrawlSummary:
    """due 소스(또는 지정 소스)를 순서대로 받는다. 예외는 소스 단위로 삼킨다."""
    seeds = registry.load_seeds()
    if source_ids:
        selected, trigger = list(source_ids), "manual"
    else:
        implemented = {sid for sid, seed in seeds.items() if registry.resolve(seed) is not None}
        selected = cadence.due_sources(seeds, implemented=implemented, now=datetime.now(KST))
        trigger = "due"
    log.info("[refresh] crawl 대상 %d개 (%s): %s", len(selected), trigger, ", ".join(selected) or "없음")

    summary = CrawlSummary(selected=selected)
    for source_id in selected:
        row_id = None if dry_run else crawl_runs.start(source_id, trigger)
        try:
            result = crawler_run.run(source_id, dry_run=dry_run)
        except Exception as e:                  # noqa: BLE001 — 한 소스가 나머지를 못 막는다
            log.exception("[refresh] crawl %s 실패", source_id)
            summary.failed += 1
            if not dry_run:
                crawl_runs.finish(row_id, "failed", error=f"{type(e).__name__}: {e}")
            continue
        if result.unavailable:
            log.warning("[refresh] crawl %s 수집 불가 — %s", source_id, result.unavailable)
            summary.unavailable += 1
            if not dry_run:
                crawl_runs.finish(row_id, "unavailable", error=result.unavailable)
            continue
        summary.ok += 1
        counts = {"fetched": result.fetched, "changed": result.changed,
                  "failed": result.failed, "skipped": result.skipped}
        log.info("[refresh] crawl %s %s", source_id, counts)
        if not dry_run:
            crawl_runs.finish(row_id, "ok", run_id=result.run_id, counts=counts,
                              changed_slugs=result.changed_slugs)
    return summary


def run_parse(*, dry_run: bool) -> int:
    return rag_cli.cmd_parse(argparse.Namespace(
        source=None, limit=0, force=False, dry_run=dry_run, verbose=False))


def run_chunk(*, dry_run: bool) -> int:
    return rag_cli.cmd_chunk(argparse.Namespace(
        source=None, force=False, dry_run=dry_run, verbose=False))


def run_embed(*, full: bool, dry_run: bool) -> int:
    """서빙 모델 하나만. `--full` 은 증분을 버리고 전량 (RAG-064)."""
    return rag_cli.cmd_embed(argparse.Namespace(
        model=rag_config.settings.embedding_model_key, all_models=False, batch=8,
        force=False, guard_only=False, dry_run=dry_run, quiet=True, full=full,
        backfill_hashes=False, restamp=False))


__all__ = ["ORDER", "CrawlSummary", "parse_stages", "run_crawl", "run_parse", "run_chunk", "run_embed"]
```

- [x] **Step 4: 통과 확인**

Run: `cd backend && uv run pytest tests/test_jobs_stages.py -q`
Expected: `8 passed`. 만약 `from daengs_life.rag import __main__` 에서 argparse 부작용이 있으면(모듈 최상위에서 `main()` 을 부르는 경우) `rag/__main__.py` 끝의 `if __name__ == "__main__":` 가드가 있는지 확인한다. 있어야 한다 — 없으면 그 가드를 추가하는 것이 이 태스크에 포함된다.

- [x] **Step 5: 의존 방향 테스트에 `jobs` 추가**

`backend/tests/test_import_direction_packages.py` 의 `ALLOWED` 에 `"tasks"` 항목 바로 아래:

```python
    # `jobs` (D-062) — Cloud Run Job 이 부르는 조립층. `tasks` 와 같은 자리에서 같은 셋을 쓴다.
    # `tasks` 와 달리 파싱~적재까지 이어 붙이므로 `rag` 도 부르지만, `rag` 는 crawler 가 아니라
    # 이 검사의 대상이 아니다. `store`·`fetch` 는 여전히 밖이다 — `run` 을 우회하는 두 번째
    # 수집 경로를 만들지 않는다.
    "jobs": {"crawler.core.config", "crawler.core.cadence", "crawler.core.registry", "crawler.run"},
```

Run: `cd backend && uv run pytest tests/test_import_direction_packages.py -q`
Expected: `3 passed`

- [x] **Step 6: 커밋**

```bash
git add backend/src/daengs_life/jobs/stages.py backend/tests/test_jobs_stages.py backend/tests/test_import_direction_packages.py
git commit -m "feat(jobs): 단계 실행 함수 — crawler.run 과 rag cmd_* 를 순서대로 (D-062)"
```

---

### Task 3: 가드가 붙은 적재

**Files:**
- Create: `backend/src/daengs_life/jobs/load.py`
- Test: `backend/tests/test_jobs_load.py`

**Interfaces:**
- Consumes: `daengs_life.rag.stages.load` 의 `prepare(model_key) -> Prepared(rows, merged, model_repo)`, `connect()`, `count(conn)`, `metadata_loss(conn, rows)`, `upsert(conn, rows)`, `stale(conn, rows)`, `prune(conn, targets)`. `jobs.guard.check`.
- Produces: `run_load(*, dry_run: bool, max_drop: float) -> int` (종료 코드). `LoadReport(before, after, upserted, pruned, verdict)`.

- [x] **Step 1: 실패하는 테스트**

`backend/tests/test_jobs_load.py`:

```python
"""적재 조립 — prepare → count → metadata_loss → guard → 한 트랜잭션(upsert + prune).
DB 는 가짜 연결로 바꾸고 **호출 순서와 트랜잭션 경계**만 본다."""
from contextlib import contextmanager
from types import SimpleNamespace

from daengs_life.jobs import load as jobs_load


class FakeConn:
    def __init__(self, before: int, after: int, stale_rows: list):
        self.calls: list[str] = []
        self._counts = iter([before, after])
        self._stale = stale_rows
        self.tx_depth = 0

    @contextmanager
    def transaction(self):
        self.tx_depth += 1
        self.calls.append(f"tx{self.tx_depth}")
        try:
            yield
        finally:
            self.tx_depth -= 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _wire(monkeypatch, *, before, after, losing, stale_rows, rows):
    conn = FakeConn(before, after, stale_rows)
    L = jobs_load.loader
    monkeypatch.setattr(L, "prepare", lambda key: SimpleNamespace(rows=rows, merged=0, model_repo="m"))
    monkeypatch.setattr(L, "connect", lambda: conn)
    monkeypatch.setattr(L, "count", lambda c: next(c._counts))
    monkeypatch.setattr(L, "metadata_loss", lambda c, r: losing)
    monkeypatch.setattr(L, "upsert", lambda c, r: c.calls.append(f"upsert{len(r)}"))
    monkeypatch.setattr(L, "stale", lambda c, r: c._stale)
    monkeypatch.setattr(L, "prune", lambda c, t: c.calls.append(f"prune{len(t)}") or len(t))
    return conn


def test_happy_path_upserts_and_prunes_inside_one_transaction(monkeypatch):
    rows = [{"content_hash": "h1"}, {"content_hash": "h2"}]
    conn = _wire(monkeypatch, before=2, after=2, losing=[], stale_rows=[("h9", "old")], rows=rows)
    assert jobs_load.run_load(dry_run=False, max_drop=0.2) == 0
    assert conn.calls == ["tx1", "upsert2", "prune1"]


def test_guard_blocks_before_any_write(monkeypatch):
    rows = [{"content_hash": "h1"}]
    conn = _wire(monkeypatch, before=10, after=10, losing=[], stale_rows=[], rows=rows)
    assert jobs_load.run_load(dry_run=False, max_drop=0.2) == 1
    assert conn.calls == []


def test_metadata_loss_blocks(monkeypatch):
    rows = [{"content_hash": "h1"}] * 10
    conn = _wire(monkeypatch, before=10, after=10, losing=[("org", 5, 0)], stale_rows=[], rows=rows)
    assert jobs_load.run_load(dry_run=False, max_drop=0.2) == 1
    assert conn.calls == []


def test_dry_run_never_connects(monkeypatch):
    rows = [{"content_hash": "h1"}]
    monkeypatch.setattr(jobs_load.loader, "prepare",
                        lambda key: SimpleNamespace(rows=rows, merged=0, model_repo="m"))
    monkeypatch.setattr(jobs_load.loader, "connect",
                        lambda: (_ for _ in ()).throw(AssertionError("dry-run 은 DB 를 안 연다")))
    assert jobs_load.run_load(dry_run=True, max_drop=0.2) == 0


def test_prepare_failure_is_exit_1(monkeypatch):
    def boom(key):
        raise FileNotFoundError("parquet 없음")
    monkeypatch.setattr(jobs_load.loader, "prepare", boom)
    assert jobs_load.run_load(dry_run=False, max_drop=0.2) == 1
```

- [x] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_jobs_load.py -q`
Expected: `ModuleNotFoundError: No module named 'daengs_life.jobs.load'`

- [x] **Step 3: 구현**

`backend/src/daengs_life/jobs/load.py`:

```python
"""가드가 붙은 적재 (D-062). `rag load` 의 `cmd_load` 와 같은 재료로 다르게 조립한다.

다른 점 둘 —
① **가드가 먼저다.** `cmd_load` 는 upsert 뒤에 stale 을 보여 주고 `--prune` 을 사람이 붙이는데,
   여기는 사람이 없다. 그래서 적재 뒤 행 수를 **먼저 계산해** 급감이면 아무것도 안 쓴다.
② **upsert 와 prune 이 한 트랜잭션이다.** `cmd_load` 는 둘이 따로 커밋된다(사람이 사이에서
   본다). 여기서 둘 사이에 죽으면 stale 행이 남은 채 끝나는데, 그건 RAG-045 가 사람이 우연히
   발견했던 바로 그 상태라 묶는다. `loader.upsert`·`prune` 의 안쪽 `conn.transaction()` 은
   psycopg3 가 세이브포인트로 중첩시키므로 바깥 하나가 전체를 되돌린다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from daengs_life.rag.core import config as rag_config
from daengs_life.rag.stages import load as loader

from . import guard

log = logging.getLogger(__name__)


@dataclass
class LoadReport:
    before: int
    after: int
    upserted: int
    pruned: int
    verdict: guard.Verdict


def run_load(*, dry_run: bool, max_drop: float) -> int:
    key = rag_config.settings.embedding_model_key
    try:
        prepared = loader.prepare(key)
    except (FileNotFoundError, ValueError) as exc:
        log.error("[refresh] load 준비 실패 — %s", exc)
        return 1
    planned = len(prepared.rows)
    log.info("[refresh] load 모델 %s 행 %d (중복 합침 %d)", key, planned, prepared.merged)
    if dry_run:
        log.info("[refresh] load dry-run — DB 를 열지 않는다")
        return 0

    with loader.connect() as conn:
        before = loader.count(conn)
        losing = loader.metadata_loss(conn, prepared.rows)
        verdict = guard.check(before, planned, losing, max_drop=max_drop)
        if not verdict.ok:
            for r in verdict.reasons:
                log.error("[refresh] load 가드 — %s", r)
            log.error("[refresh] load 중단. DB 는 그대로다 (documents %d행)", before)
            return 1

        with conn.transaction():
            loader.upsert(conn, prepared.rows)
            left = loader.stale(conn, prepared.rows)
            pruned = loader.prune(conn, left) if left else 0
        after = loader.count(conn)

    report = LoadReport(before=before, after=after, upserted=planned, pruned=pruned, verdict=verdict)
    log.info("[refresh] load 끝 — documents %d → %d (upsert %d · prune %d)",
             report.before, report.after, report.upserted, report.pruned)
    return 0


__all__ = ["LoadReport", "run_load"]
```

- [x] **Step 4: 통과 확인**

Run: `cd backend && uv run pytest tests/test_jobs_load.py -q`
Expected: `5 passed`

- [x] **Step 5: 커밋**

```bash
git add backend/src/daengs_life/jobs/load.py backend/tests/test_jobs_load.py
git commit -m "feat(jobs): 가드 뒤 한 트랜잭션 적재 — upsert 와 prune 을 묶는다 (D-062)"
```

---

### Task 4: 동시 실행 확인

**Files:**
- Create: `backend/src/daengs_life/jobs/lock.py`
- Test: `backend/tests/test_jobs_lock.py`
- Modify: `backend/pyproject.toml` (via `uv add`), `backend/uv.lock`

**Interfaces:**
- Produces: `lock.another_execution_running(*, job: str | None, execution: str | None, project: str | None, region: str | None, list_executions=None) -> str | None`. 다른 실행이 있으면 그 이름, 없거나 로컬(잡 env 없음)이면 `None`.

- [x] **Step 1: 의존성**

```bash
cd backend && uv add --group pipeline google-cloud-run
```

`[dependency-groups]` 에 `pipeline` 그룹이 생긴다. `ml` 에 넣지 않는 이유: 개발 PC 의 `uv sync --group ml` 이 GCP 클라이언트까지 끌고 오지 않게. 이미지는 `--group ml --group pipeline`.

- [x] **Step 2: 실패하는 테스트**

`backend/tests/test_jobs_lock.py`:

```python
"""동시 실행 확인 — Cloud Run API 응답을 가짜로 주고 판정만 본다 (D-062 §5)."""
from types import SimpleNamespace

from daengs_life.jobs import lock

ME = "projects/p/locations/asia-northeast3/jobs/corpus-refresh/executions/corpus-refresh-abc"
OTHER = "projects/p/locations/asia-northeast3/jobs/corpus-refresh/executions/corpus-refresh-xyz"


def _ex(name, running):
    return SimpleNamespace(name=name, running_count=1 if running else 0,
                           completion_time=None if running else object())


def test_local_run_without_job_env_is_never_locked():
    assert lock.another_execution_running(job=None, execution=None, project=None, region=None) is None


def test_only_myself_running_is_fine():
    found = lock.another_execution_running(
        job="corpus-refresh", execution="corpus-refresh-abc", project="p", region="asia-northeast3",
        list_executions=lambda parent: [_ex(ME, True)])
    assert found is None


def test_another_running_execution_is_reported():
    found = lock.another_execution_running(
        job="corpus-refresh", execution="corpus-refresh-abc", project="p", region="asia-northeast3",
        list_executions=lambda parent: [_ex(ME, True), _ex(OTHER, True)])
    assert found == "corpus-refresh-xyz"


def test_finished_executions_do_not_count():
    found = lock.another_execution_running(
        job="corpus-refresh", execution="corpus-refresh-abc", project="p", region="asia-northeast3",
        list_executions=lambda parent: [_ex(ME, True), _ex(OTHER, False)])
    assert found is None


def test_parent_path_is_built_from_project_region_job():
    seen = {}
    lock.another_execution_running(
        job="corpus-refresh", execution="e", project="p", region="r",
        list_executions=lambda parent: seen.setdefault("parent", parent) and [])
    assert seen["parent"] == "projects/p/locations/r/jobs/corpus-refresh"
```

- [x] **Step 3: 실패 확인**

Run: `cd backend && uv run pytest tests/test_jobs_lock.py -q`
Expected: `ModuleNotFoundError: No module named 'daengs_life.jobs.lock'`

- [x] **Step 4: 구현**

`backend/src/daengs_life/jobs/lock.py`:

```python
"""같은 잡이 이미 돌고 있으면 건너뛴다 (D-062 §5).

Cloud Run Job 에는 "동시 실행 1개" 설정이 없다. Scheduler 발사와 관리자 트리거(#326)가 겹치면
두 잡이 같은 버킷에 쓰고 해시 파일이 꼬인다. 버킷 잠금 파일 대신 API 를 묻는 이유 — 잡이 죽어
잠금이 남는 문제가 없다(실행이 끝나면 API 가 그렇게 답한다).

Cloud Run 이 잡 컨테이너에 넣어 주는 env: `CLOUD_RUN_JOB`(잡 이름) · `CLOUD_RUN_EXECUTION`(이번
실행 이름). 프로젝트·리전은 자동으로 안 오므로 잡 정의에서 `DAENGS_GCP_PROJECT`·`DAENGS_GCP_REGION`
으로 넣는다 (`infra/gcp/pipeline.sh`). 넷 중 하나라도 없으면 로컬 실행으로 보고 확인을 건너뛴다.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable

log = logging.getLogger(__name__)


def _default_list_executions(parent: str) -> Iterable:
    from google.cloud import run_v2

    client = run_v2.ExecutionsClient()
    return client.list_executions(parent=parent)


def another_execution_running(*, job: str | None, execution: str | None,
                              project: str | None, region: str | None,
                              list_executions: Callable[[str], Iterable] | None = None) -> str | None:
    """다른 실행이 돌고 있으면 그 짧은 이름, 아니면 None."""
    if not (job and execution and project and region):
        return None
    parent = f"projects/{project}/locations/{region}/jobs/{job}"
    lister = list_executions or _default_list_executions
    for ex in lister(parent):
        short = ex.name.rsplit("/", 1)[-1]
        if short == execution:
            continue
        # 끝난 실행은 completion_time 이 있다. 도는 것만 센다.
        if getattr(ex, "completion_time", None) is None and getattr(ex, "running_count", 0) > 0:
            return short
    return None


def from_env() -> str | None:
    return another_execution_running(
        job=os.environ.get("CLOUD_RUN_JOB"),
        execution=os.environ.get("CLOUD_RUN_EXECUTION"),
        project=os.environ.get("DAENGS_GCP_PROJECT"),
        region=os.environ.get("DAENGS_GCP_REGION"),
    )


__all__ = ["another_execution_running", "from_env"]
```

- [x] **Step 5: 통과 확인**

Run: `cd backend && uv run pytest tests/test_jobs_lock.py -q`
Expected: `5 passed`

- [x] **Step 6: 커밋**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src/daengs_life/jobs/lock.py backend/tests/test_jobs_lock.py
git commit -m "feat(jobs): 동시 실행 확인 — Cloud Run API 로 같은 잡의 다른 실행을 본다 (D-062)"
```

---

### Task 5: CLI 진입점 `corpus-refresh`

**Files:**
- Create: `backend/src/daengs_life/jobs/corpus_refresh.py`
- Modify: `backend/pyproject.toml` `[project.scripts]` (여기는 스크립트 등록이라 직접 편집 — 의존성이 아니다)
- Test: `backend/tests/test_jobs_corpus_refresh.py`

**Interfaces:**
- Consumes: Task 2 의 `stages.*`, Task 3 의 `load.run_load`, Task 4 의 `lock.from_env`.
- Produces: `corpus_refresh.main(argv: list[str] | None = None) -> int`. 인자 `--stages` `--sources a,b` `--full` `--dry-run` `--max-drop 0.2` `--seeds-from PATH`.

- [x] **Step 1: 실패하는 테스트**

`backend/tests/test_jobs_corpus_refresh.py`:

```python
"""진입점 — 인자를 단계 호출로 바꾸고, 실패한 단계에서 멈추는지."""
from daengs_life.jobs import corpus_refresh as cr
from daengs_life.jobs import stages


def _wire(monkeypatch, calls, *, fail_at=None):
    monkeypatch.setattr(cr.lock, "from_env", lambda: None)

    def rec(name, rc=0):
        def f(*a, **kw):
            calls.append((name, kw))
            if name == fail_at:
                return 1
            return rc
        return f

    monkeypatch.setattr(cr.stages, "run_crawl",
                        lambda sources, *, dry_run: calls.append(("crawl", {"sources": sources, "dry_run": dry_run}))
                        or stages.CrawlSummary(selected=list(sources or []), ok=1))
    monkeypatch.setattr(cr.stages, "run_parse", rec("parse"))
    monkeypatch.setattr(cr.stages, "run_chunk", rec("chunk"))
    monkeypatch.setattr(cr.stages, "run_embed", rec("embed"))
    monkeypatch.setattr(cr.jobs_load, "run_load", rec("load"))


def test_default_runs_all_five_in_order(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    assert cr.main([]) == 0
    assert [c[0] for c in calls] == ["crawl", "parse", "chunk", "embed", "load"]


def test_stops_at_first_failing_stage(monkeypatch):
    calls = []
    _wire(monkeypatch, calls, fail_at="chunk")
    assert cr.main([]) == 1
    assert [c[0] for c in calls] == ["crawl", "parse", "chunk"]


def test_flags_reach_the_stages(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    assert cr.main(["--stages", "embed,load", "--full", "--dry-run", "--max-drop", "0.5"]) == 0
    assert calls == [("embed", {"full": True, "dry_run": True}),
                     ("load", {"dry_run": True, "max_drop": 0.5})]


def test_sources_go_to_crawl_only(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    cr.main(["--stages", "crawl", "--sources", "a, b"])
    assert calls == [("crawl", {"sources": ["a", "b"], "dry_run": False})]


def test_all_sources_failed_stops_pipeline(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    monkeypatch.setattr(cr.stages, "run_crawl",
                        lambda sources, *, dry_run: stages.CrawlSummary(selected=["a"], failed=1))
    assert cr.main([]) == 1
    assert calls == []


def test_no_due_sources_is_not_a_failure(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    monkeypatch.setattr(cr.stages, "run_crawl", lambda sources, *, dry_run: stages.CrawlSummary())
    assert cr.main(["--stages", "crawl"]) == 0


def test_skips_when_another_execution_is_running(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    monkeypatch.setattr(cr.lock, "from_env", lambda: "corpus-refresh-xyz")
    assert cr.main([]) == 0
    assert calls == []


def test_seeds_are_copied_into_data_dir(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, calls)
    src = tmp_path / "seed_sources.yaml"
    src.write_text("- id: a\n", encoding="utf-8")
    data = tmp_path / "data"
    monkeypatch.setattr(cr, "_manifest_dir", lambda: data / "manifests")
    cr.main(["--stages", "parse", "--seeds-from", str(src)])
    assert (data / "manifests" / "seed_sources.yaml").read_text(encoding="utf-8") == "- id: a\n"
```

- [x] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_jobs_corpus_refresh.py -q`
Expected: `ModuleNotFoundError: No module named 'daengs_life.jobs.corpus_refresh'`

- [x] **Step 3: 구현**

`backend/src/daengs_life/jobs/corpus_refresh.py`:

```python
"""`corpus-refresh` — Cloud Run Job 의 진입점 (D-062).

    corpus-refresh                              # crawl(due) → parse → chunk → embed → load
    corpus-refresh --stages embed --full        # 전체 재임베딩 (GPU 잡이 이것을 부른다)
    corpus-refresh --stages crawl --sources a,b # 지정 소스만 (관리자 트리거, #326)
    corpus-refresh --dry-run                    # 아무것도 쓰지 않고 끝까지

종료 코드: 0 = 끝났거나 건너뜀 · 1 = 어느 단계가 실패해 거기서 멈춤. Cloud Run 은 0 이 아니면
실행을 실패로 표시하고 Monitoring 알림이 그것을 본다 — 그래서 **재시도는 안 건다.**
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from daengs_life.crawler.core import config as crawler_config

from . import load as jobs_load
from . import lock, stages

log = logging.getLogger("daengs_life.jobs")


def _manifest_dir() -> Path:
    return crawler_config.require_data_dir() / "manifests"


def _install_seeds(src: Path) -> None:
    """git 의 시드를 코퍼스 폴더에 덮어쓴다. compose 가 파일 하나를 겹쳐 마운트하던 것과 같은 뜻 —
    시드는 코퍼스가 아니라 코드다 (RAG-050). 버킷의 낡은 시드가 이기면 안 된다."""
    dst = _manifest_dir() / "seed_sources.yaml"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    log.info("[refresh] seeds %s → %s", src, dst)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="corpus-refresh", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stages", help=f"쉼표로: {','.join(stages.ORDER)} (기본 전부)")
    p.add_argument("--sources", help="crawl 단계에서 due 판정 대신 이 소스만 (쉼표)")
    p.add_argument("--full", action="store_true", help="embed 를 증분 없이 전량")
    p.add_argument("--dry-run", action="store_true", help="아무것도 쓰지 않는다 (DB 도 안 연다)")
    p.add_argument("--max-drop", type=float, default=0.2,
                   help="load 가드: 행 수가 이 비율 이상 줄면 중단 (기본 0.2)")
    p.add_argument("--seeds-from", type=Path,
                   help="이 시드 파일을 DATA_DIR/manifests/ 에 복사한 뒤 시작 (이미지의 git 사본)")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)
    args = _parser().parse_args(argv)
    try:
        wanted = stages.parse_stages(args.stages)
    except ValueError as e:
        log.error("%s", e)
        return 2

    if (other := lock.from_env()):
        log.warning("[refresh] 다른 실행 %s 이 돌고 있다 — 이번은 건너뛴다", other)
        return 0

    if args.seeds_from:
        _install_seeds(args.seeds_from)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None
    log.info("[refresh] 시작 — 단계 %s%s%s", ",".join(wanted),
             " · dry-run" if args.dry_run else "", " · full" if args.full else "")

    for stage in wanted:
        log.info("[refresh] ▶ %s", stage)
        if stage == "crawl":
            s = stages.run_crawl(sources, dry_run=args.dry_run)
            log.info("[refresh] crawl 끝 — 대상 %d · ok %d · failed %d · unavailable %d",
                     len(s.selected), s.ok, s.failed, s.unavailable)
            if s.selected and s.ok == 0 and s.failed > 0:
                log.error("[refresh] crawl 전부 실패 — 멈춘다")
                return 1
            continue
        if stage == "parse":
            rc = stages.run_parse(dry_run=args.dry_run)
        elif stage == "chunk":
            rc = stages.run_chunk(dry_run=args.dry_run)
        elif stage == "embed":
            rc = stages.run_embed(full=args.full, dry_run=args.dry_run)
        else:
            rc = jobs_load.run_load(dry_run=args.dry_run, max_drop=args.max_drop)
        if rc != 0:
            log.error("[refresh] %s 실패 (rc=%d) — 멈춘다", stage, rc)
            return 1

    log.info("[refresh] 끝")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`backend/pyproject.toml` 의 `[project.scripts]` 에 `kakao-token` 줄 아래:

```toml
# 코퍼스 파이프라인 — Cloud Run Job 이 부른다 (D-062). Celery 없이 crawl~load 를 한 프로세스에서.
corpus-refresh = "daengs_life.jobs.corpus_refresh:main"
```

스크립트를 등록했으니 `cd backend && uv sync --group ml --group pipeline` 으로 엔트리포인트를 다시 만든다 (`uv run corpus-refresh --help` 가 떠야 한다).

- [x] **Step 4: 통과 확인**

Run: `cd backend && uv run pytest tests/test_jobs_corpus_refresh.py -q && uv run corpus-refresh --help | head -3`
Expected: `8 passed`, 그리고 `usage: corpus-refresh [-h] [--stages STAGES] ...`

- [x] **Step 5: 로컬 dry-run (임시 data 사본)**

개발 PC 의 코퍼스는 정본이 아니지만 `processed/` 가 있어 dry-run 에 충분하다. **정본 `data/` 에 쓰지 않도록** 임시 복사본을 쓴다:

```powershell
# PowerShell, 저장소 루트
$tmp = Join-Path $env:TEMP "daengs-refresh-dryrun"
robocopy data\raw "$tmp\raw" /E /NFL /NDL /NJH /NJS | Out-Null
robocopy data\manifests "$tmp\manifests" /E /NFL /NDL /NJH /NJS | Out-Null
robocopy data\processed "$tmp\processed" /E /NFL /NDL /NJH /NJS /XD answers eval | Out-Null
cd backend
$env:DAENGS_DATA_DIR = $tmp
uv run corpus-refresh --stages parse,chunk,embed,load --dry-run
Remove-Item Env:DAENGS_DATA_DIR
```

Expected: 네 단계가 `[refresh] ▶ …` 로 지나가고 마지막에 `[refresh] 끝`, 종료 코드 0. `crawl` 은 실제 요청을 내므로 여기서는 뺀다.

- [x] **Step 6: 커밋**

```bash
git add backend/src/daengs_life/jobs/corpus_refresh.py backend/pyproject.toml backend/uv.lock backend/tests/test_jobs_corpus_refresh.py
git commit -m "feat(jobs): corpus-refresh 진입점 — crawl~load 를 한 프로세스에서 (D-062)"
```

---

### Task 6: 파이프라인 이미지

**Files:**
- Create: `docker/pipeline/Dockerfile`
- Create: `docker/pipeline/entrypoint.sh`
- Create: `docker/pipeline/.dockerignore` (빌드 컨텍스트가 저장소 루트라 여기 둔다 — `docker build -f docker/pipeline/Dockerfile .`; Docker 는 `-f` 옆의 `.dockerignore` 를 `<Dockerfile>.dockerignore` 이름일 때만 읽으므로 파일명은 `docker/pipeline/Dockerfile.dockerignore`)

**Interfaces:**
- Produces: 이미지 `pipeline:cpu` · `pipeline:cuda`. ENTRYPOINT 가 `entrypoint.sh`, CMD 없음. 인자는 그대로 `corpus-refresh` 로 간다. 환경: `DAENGS_DATA_DIR=/data`, `HF_HOME=/models`.

- [x] **Step 1: Dockerfile**

`docker/pipeline/Dockerfile`:

```dockerfile
# 코퍼스 파이프라인 이미지 (D-062). Cloud Run Job `corpus-refresh`(CPU) · `corpus-embed-full`(CUDA).
#
#   docker build -f docker/pipeline/Dockerfile -t pipeline:cpu  .
#   docker build -f docker/pipeline/Dockerfile -t pipeline:cuda --build-arg TORCH=cuda .
#
# 빌드 컨텍스트는 **저장소 루트**다 — backend/ 와 data/manifests/seed_sources.yaml 둘 다 필요해서.
#
# lock 의 torch 는 리눅스에서 CPU 인덱스로 고정돼 있다 (backend/pyproject.toml [tool.uv.sources]).
# 서버 backend 컨테이너가 NVIDIA 런타임 10GB 를 받지 않게 한 결정이라 lock 을 안 건드린다.
# CUDA 판은 sync 뒤에 **같은 버전**의 torch·torchvision 을 cu126 인덱스에서 덮어쓴다 — `uv pip`
# 은 lock 을 안 보므로 lock 은 그대로다. 버전은 lock 에서 읽어 어긋나지 않게 한다.
ARG TORCH=cpu

FROM uv:1 AS base
# uv:1 은 docker/uv/Dockerfile 로 굽는 공용 베이스 (python 3.12 + uv). 로컬에 없으면
#   docker build -t uv:1 docker/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    HF_HOME=/models \
    DAENGS_DATA_DIR=/data \
    DAENGS_WARM_UP_ENCODER=false

# 의존성만 먼저 — 소스가 바뀌어도 이 레이어는 캐시된다
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-install-project --group ml --group pipeline

FROM base AS torch-cpu
# lock 그대로 (CPU torch)

FROM base AS torch-cuda
RUN set -eux; \
    V_TORCH="$(uv pip show torch | awk '/^Version/ {print $2}')"; \
    V_TV="$(uv pip show torchvision | awk '/^Version/ {print $2}')"; \
    uv pip install --index-url https://download.pytorch.org/whl/cu126 \
        "torch==${V_TORCH}" "torchvision==${V_TV}"

FROM torch-${TORCH} AS final
COPY backend/src ./src
COPY data/manifests/seed_sources.yaml /app/seed_sources.yaml
RUN uv sync --frozen --group ml --group pipeline     # 프로젝트 자체(엔트리포인트) 설치

# 가중치를 굽는다 — 잡이 뜰 때마다 1.2GB 를 받지 않게. 모델 키는 rag/core/config.py 기본값과 같다.
RUN uv run --no-sync python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('Qwen/Qwen3-Embedding-0.6B', device='cpu')"

COPY docker/pipeline/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
```

- [x] **Step 2: entrypoint 와 dockerignore**

`docker/pipeline/entrypoint.sh`:

```sh
#!/bin/sh
# 코퍼스 폴더 가드 (RAG-050): 로그가 없으면 전 소스가 due 로 잡혀 다른 코퍼스가 된다.
# --dry-run 이나 --stages 에 crawl 이 없는 실행은 이 가드가 필요 없지만, 단순하게 항상 본다 —
# 버킷 마운트가 비어 있는 것 자체가 사고다.
set -eu
if [ ! -f /data/manifests/crawl_log.jsonl ]; then
  echo "코퍼스가 없습니다: /data/manifests/crawl_log.jsonl 이 없습니다. 버킷 마운트를 확인하세요." >&2
  exit 1
fi
exec uv run --no-sync corpus-refresh --seeds-from /app/seed_sources.yaml "$@"
```

`docker/pipeline/Dockerfile.dockerignore`:

```
*
!backend/pyproject.toml
!backend/uv.lock
!backend/README.md
!backend/src
!data/manifests/seed_sources.yaml
!docker/pipeline/entrypoint.sh
backend/src/**/__pycache__
```

- [x] **Step 3: CPU 이미지 빌드와 dry-run**

```powershell
# 저장소 루트. uv:1 이 없으면 먼저: docker build -t uv:1 docker/uv
docker build -f docker/pipeline/Dockerfile -t pipeline:cpu .
docker images pipeline:cpu --format "{{.Size}}"
# Task 5 Step 5 의 임시 사본을 /data 로 마운트해 dry-run
$tmp = Join-Path $env:TEMP "daengs-refresh-dryrun"
docker run --rm -v "${tmp}:/data" pipeline:cpu --stages parse,chunk,embed,load --dry-run
```

Expected: 빌드 성공, 크기 3~4GB, 컨테이너 안에서 네 단계가 dry-run 으로 지나가고 종료 코드 0. `embed` 가 모델을 `/models` 에서 읽어 네트워크 없이 뜨는지 로그로 확인 (`HF_HUB_OFFLINE=1` 을 `-e` 로 주고 한 번 더 돌리면 확실하다).

- [x] **Step 4: 커밋**

```bash
git add docker/pipeline/
git commit -m "build: 코퍼스 파이프라인 이미지 — ml 그룹 + 가중치 굽기, CPU/CUDA 빌드 인자 (D-062)"
```

CUDA 빌드는 로컬에서 안 한다(7GB, 개발 PC 에서 검증할 것이 없다). Task 7 에서 Cloud Build 로 굽는다.

---

### Task 7: gcloud 스크립트

**Files:**
- Create: `infra/gcp/pipeline.sh`
- Create: `infra/gcp/pipeline-teardown.sh`
- Create: `infra/gcp/README.md`

**Interfaces:**
- Consumes: Task 6 이미지. Task 4 의 env 이름 `DAENGS_GCP_PROJECT` `DAENGS_GCP_REGION`. rag 설정 env `POSTGRES_IP` `POSTGRES_PORT` `POSTGRES_USER` `POSTGRES_PASSWORD` `POSTGRES_DB` `EMBEDDING_MODEL_KEY`. 크롤러 키 env `LAW_OC` `DATA_GO_KR_KEY`.
- Produces: GCP 리소스 이름 — 스펙 §4 표 그대로.

- [x] **Step 1: 생성 스크립트**

`infra/gcp/pipeline.sh` (VM 이 아니라 **개발 PC 의 gcloud** 로, 또는 Cloud Shell 에서 실행. bash):

```bash
#!/usr/bin/env bash
# GCP 코퍼스 파이프라인 리소스 (D-062, docs/deploy/corpus-pipeline.md §4).
# 여러 번 돌려도 안전하게 — 있으면 건너뛰거나 update 한다.
#
#   PROJECT=my-proj VM_INTERNAL_IP=10.178.0.2 bash infra/gcp/pipeline.sh
#
# 사람이 먼저 할 것 (infra/gcp/README.md): API 켜기 · Secret 값 넣기 · 초기 사본 업로드.
set -euo pipefail

: "${PROJECT:?GCP 프로젝트 id}"
: "${VM_INTERNAL_IP:?pgvector 가 있는 VM 의 내부 IP (gcloud compute instances list)}"
REGION=asia-northeast3          # 서울 — 버킷·refresh 잡·Scheduler
GPU_REGION=asia-southeast1      # 싱가포르 — Cloud Run Jobs 의 L4 가 있는 가장 가까운 리전
BUCKET="daengs-corpus"
REPO="daengs"
SA="corpus-pipeline"
SA_EMAIL="${SA}@${PROJECT}.iam.gserviceaccount.com"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/pipeline"
SHA="$(git rev-parse --short HEAD)"

gcloud config set project "${PROJECT}" >/dev/null

echo "== APIs"
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com storage.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com cloudbuild.googleapis.com \
  monitoring.googleapis.com logging.googleapis.com

echo "== 버킷 (버전 관리 켬 — 잘못된 적재를 되돌리는 유일한 길)"
gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://${BUCKET}" --location="${REGION}" --uniform-bucket-level-access
gcloud storage buckets update "gs://${BUCKET}" --versioning

echo "== Artifact Registry"
gcloud artifacts repositories describe "${REPO}" --location="${REGION}" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "${REPO}" --location="${REGION}" --repository-format=docker

echo "== 서비스 계정"
gcloud iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${SA}" --display-name="corpus pipeline job"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA_EMAIL}" --role=roles/storage.objectAdmin >/dev/null
gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${SA_EMAIL}" --role=roles/run.viewer >/dev/null       # 동시 실행 확인
for s in corpus-db-password corpus-law-oc corpus-data-go-kr-key; do
  gcloud secrets describe "$s" >/dev/null 2>&1 || gcloud secrets create "$s" --replication-policy=automatic
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:${SA_EMAIL}" \
    --role=roles/secretmanager.secretAccessor >/dev/null
done

echo "== 방화벽: 서울 서브넷 → VM 5432 (인터넷에는 여전히 안 연다)"
SUBNET_RANGE="$(gcloud compute networks subnets describe default --region="${REGION}" --format='value(ipCidrRange)')"
gcloud compute firewall-rules describe allow-pg-from-run >/dev/null 2>&1 || \
  gcloud compute firewall-rules create allow-pg-from-run --network=default --direction=INGRESS \
    --action=ALLOW --rules=tcp:5432 --source-ranges="${SUBNET_RANGE}" \
    --description="Cloud Run corpus job -> pgvector on VM (D-062)"

echo "== 이미지 (Cloud Build 가 굽는다 — 개발 PC 에서 7GB 를 올리지 않는다)"
# `--tag` 는 루트의 Dockerfile 만 보므로 config 로 -f 를 준다. 컨텍스트는 저장소 루트.
build_image() {   # $1 = cpu|cuda
  gcloud builds submit . --timeout=2400 --config=- <<CFG
steps:
- name: gcr.io/cloud-builders/docker
  args: ['build','-f','docker/pipeline/Dockerfile','--build-arg','TORCH=$1','-t','${IMAGE_BASE}:$1-${SHA}','.']
images: ['${IMAGE_BASE}:$1-${SHA}']
options:
  machineType: E2_HIGHCPU_8
CFG
}
build_image cpu
build_image cuda

COMMON_ENV="DAENGS_GCP_PROJECT=${PROJECT},POSTGRES_IP=${VM_INTERNAL_IP},POSTGRES_PORT=5432,POSTGRES_USER=daengs,POSTGRES_DB=vectordb,EMBEDDING_MODEL_KEY=qwen3-embedding-0.6b"
COMMON_SECRETS="POSTGRES_PASSWORD=corpus-db-password:latest,LAW_OC=corpus-law-oc:latest,DATA_GO_KR_KEY=corpus-data-go-kr-key:latest"

echo "== Job corpus-refresh (서울, CPU)"
gcloud run jobs deploy corpus-refresh --region="${REGION}" --image="${IMAGE_BASE}:cpu-${SHA}" \
  --service-account="${SA_EMAIL}" --cpu=4 --memory=16Gi --task-timeout=3h --max-retries=0 \
  --network=default --subnet=default --vpc-egress=private-ranges-only \
  --add-volume=name=corpus,type=cloud-storage,bucket="${BUCKET}" --add-volume-mount=volume=corpus,mount-path=/data \
  --set-env-vars="${COMMON_ENV},DAENGS_GCP_REGION=${REGION}" --set-secrets="${COMMON_SECRETS}"

echo "== Job corpus-embed-full (싱가포르, L4)"
gcloud run jobs deploy corpus-embed-full --region="${GPU_REGION}" --image="${IMAGE_BASE}:cuda-${SHA}" \
  --service-account="${SA_EMAIL}" --cpu=8 --memory=32Gi --gpu=1 --gpu-type=nvidia-l4 --no-gpu-zonal-redundancy \
  --task-timeout=2h --max-retries=0 \
  --add-volume=name=corpus,type=cloud-storage,bucket="${BUCKET}" --add-volume-mount=volume=corpus,mount-path=/data \
  --set-env-vars="${COMMON_ENV},DAENGS_GCP_REGION=${GPU_REGION}" --set-secrets="${COMMON_SECRETS}" \
  --args="--stages,embed,--full"

echo "== Scheduler (매일 04:00 KST → corpus-refresh)"
gcloud run jobs add-iam-policy-binding corpus-refresh --region="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" --role=roles/run.invoker >/dev/null
JOB_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/corpus-refresh:run"
if gcloud scheduler jobs describe corpus-refresh-daily --location="${REGION}" >/dev/null 2>&1; then
  gcloud scheduler jobs update http corpus-refresh-daily --location="${REGION}" \
    --schedule="0 4 * * *" --time-zone="Asia/Seoul" --uri="${JOB_URI}" --http-method=POST \
    --oauth-service-account-email="${SA_EMAIL}"
else
  gcloud scheduler jobs create http corpus-refresh-daily --location="${REGION}" \
    --schedule="0 4 * * *" --time-zone="Asia/Seoul" --uri="${JOB_URI}" --http-method=POST \
    --oauth-service-account-email="${SA_EMAIL}"
fi

echo "== 알림: 잡 실행 실패 → 이메일 (채널은 콘솔에서 한 번 만들어 CHANNEL 로 준다)"
if [ -n "${CHANNEL:-}" ]; then
  gcloud monitoring policies create --display-name="corpus job failed" \
    --notification-channels="${CHANNEL}" --combiner=OR \
    --condition-display-name="Cloud Run Job execution failed" \
    --condition-filter='resource.type="cloud_run_job" AND metric.type="run.googleapis.com/job/completed_task_attempt_count" AND metric.labels.result="failed"' \
    --condition-threshold-value=0 --condition-threshold-comparison=COMPARISON_GT \
    --condition-threshold-duration=0s || echo "(알림 정책은 이미 있거나 실패 — 콘솔에서 확인)"
fi

echo "끝. 다음: infra/gcp/README.md 의 '초기 사본' 과 '검증'."
```

- [x] **Step 2: 삭제 스크립트**

`infra/gcp/pipeline-teardown.sh`:

```bash
#!/usr/bin/env bash
# 11-17 실험 종료 (roadmap §8). 버킷은 **마지막에, 확인 뒤** 지운다 — 되돌릴 수 없다.
set -euo pipefail
: "${PROJECT:?}"
REGION=asia-northeast3; GPU_REGION=asia-southeast1
gcloud config set project "${PROJECT}" >/dev/null
gcloud scheduler jobs delete corpus-refresh-daily --location="${REGION}" --quiet || true
gcloud run jobs delete corpus-refresh --region="${REGION}" --quiet || true
gcloud run jobs delete corpus-embed-full --region="${GPU_REGION}" --quiet || true
gcloud compute firewall-rules delete allow-pg-from-run --quiet || true
gcloud artifacts repositories delete daengs --location="${REGION}" --quiet || true
for s in corpus-db-password corpus-law-oc corpus-data-go-kr-key; do gcloud secrets delete "$s" --quiet || true; done
gcloud iam service-accounts delete "corpus-pipeline@${PROJECT}.iam.gserviceaccount.com" --quiet || true
echo "버킷 gs://daengs-corpus 는 남겨 두었다. 정말 지우려면:"
echo "  gcloud storage rm -r gs://daengs-corpus"
```

- [x] **Step 3: README**

`infra/gcp/README.md`:

```markdown
# infra/gcp — 코퍼스 파이프라인 (D-062)

설계 `docs/deploy/corpus-pipeline.md` · 운영 절차 `docs/deploy/runbook.md` §6 "코퍼스 파이프라인 (GCP)".
**11-17 실험 종료 때 `pipeline-teardown.sh`** (roadmap §8).

## 순서

1. **사람, 콘솔** — 결제 계정이 무료 체험이 아닌지 확인. `gcloud auth login` · `gcloud config set project`.
2. **사람** — Secret 값 넣기 (스크립트가 빈 Secret 을 만들어 두므로 먼저 스크립트를 한 번 돌려도 된다):
   ```bash
   printf '%s' '<VM pgvector 의 daengs 비밀번호>' | gcloud secrets versions add corpus-db-password --data-file=-
   printf '%s' '<LAW_OC>'          | gcloud secrets versions add corpus-law-oc --data-file=-
   printf '%s' '<DATA_GO_KR_KEY>'  | gcloud secrets versions add corpus-data-go-kr-key --data-file=-
   ```
3. **사람** — VM 내부 IP: `gcloud compute instances list --format='value(name,networkInterfaces[0].networkIP)'`
4. **사람** — `PROJECT=… VM_INTERNAL_IP=… bash infra/gcp/pipeline.sh` (저장소 루트에서. 이미지 두 장을 Cloud Build 가 굽는다, 20~40분).
5. **사람** — 초기 사본. 집 서버 `DAENGS_CORPUS_DIR` 의 `raw/` 와 `manifests/crawl_log.jsonl` 만:
   ```powershell
   gcloud storage rsync -r C:\deploy\daengs\corpus\raw gs://daengs-corpus/raw
   gcloud storage cp C:\deploy\daengs\corpus\manifests\crawl_log.jsonl gs://daengs-corpus/manifests/crawl_log.jsonl
   ```
   `processed/` 는 올리지 않는다 — 잡이 만든다. `seed_sources.yaml` 도 올리지 않는다 — 이미지가 넣는다.
6. **검증** — `docs/deploy/corpus-pipeline.md` §6 의 2~6. 잡 수동 실행:
   ```bash
   gcloud run jobs execute corpus-refresh --region=asia-northeast3 --args="--stages,parse,chunk" --wait
   gcloud run jobs execute corpus-refresh --region=asia-northeast3 --args="--stages,embed,--dry-run" --wait
   gcloud run jobs execute corpus-embed-full --region=asia-southeast1 --wait          # 전체 임베딩 (GPU)
   gcloud run jobs execute corpus-refresh --region=asia-northeast3 --args="--stages,load,--dry-run" --wait
   gcloud run jobs execute corpus-refresh --region=asia-northeast3 --args="--stages,load" --wait
   gcloud run jobs execute corpus-refresh --region=asia-northeast3 --wait                 # 전체
   ```
   로그: `gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="corpus-refresh"' --limit=200 --format='value(textPayload)'`
7. 다음 날 `crawl_runs` 에 `trigger='due'` 행이 있으면 끝.

## 이미지를 다시 구울 때

코드가 바뀌면 `pipeline.sh` 를 다시 돌린다 — 태그가 `cpu-<sha>` 라 잡 정의가 새 이미지로 update 된다.

## 자주 걸리는 것

- **잡이 `코퍼스가 없습니다` 로 바로 죽는다** — 버킷에 `manifests/crawl_log.jsonl` 이 없다(5번).
- **`load` 가 connection refused** — 방화벽 규칙의 source range 가 잡이 쓰는 서브넷과 다르거나, VM 의 compose 가 5432 를 `0.0.0.0` 에 게시하지 않았다(`docker compose ps` 로 확인).
- **`embed` 가 모델을 받으려 한다** — 이미지 빌드의 가중치 굽기가 실패한 것. `HF_HOME=/models` 가 잡 env 에 있는지.
- **GPU 잡이 quota 에러** — 첫 실행에 자동 할당(3장)이 안 된 경우. 콘솔 할당량에서 `Cloud Run Admin API` × `NVIDIA L4` 를 1 로 요청.
```

- [x] **Step 4: 문법 검사와 커밋**

```bash
bash -n infra/gcp/pipeline.sh && bash -n infra/gcp/pipeline-teardown.sh && echo ok
git add infra/gcp/
git commit -m "infra: GCP 코퍼스 파이프라인 gcloud 스크립트 — 잡 2개·Scheduler·버킷·Secret·방화벽 (D-062)"
```

실행은 **사람이** 한다 (README 순서). 이 태스크의 완료는 문법 검사까지다.

---

### Task 8: 사람 손 + 검증 (코드 없음)

**Files:** 없음. 결과는 PR #325 본문 "확인한 것" 에 숫자로.

- [ ] **Step 1: 사람** — `infra/gcp/README.md` 1~5 (Secret · 스크립트 · 초기 사본)
- [ ] **Step 2: 검증 2** — `--stages parse,chunk` 잡. 버킷에 `processed/parsed/` `processed/chunks/` 가 생겼는지 `gcloud storage ls gs://daengs-corpus/processed/`
- [ ] **Step 3: 검증 3** — `--stages embed --dry-run` 잡. 로그에 `guard ok` 와 `encoding … 건` 이 찍히고 메모리 OOM 없이 끝나는지 (`gcloud run jobs executions describe`)
- [ ] **Step 4: 검증 4** — `corpus-embed-full` 잡. 실행 시간과 `written … 전량 N행` 의 N 을 기록
- [ ] **Step 5: 검증 5** — `--stages load --dry-run` → `--stages load`. 로그의 `documents A → B` 와 집 서버 `SELECT count(*) FROM documents` 를 대조. 카테고리 분포는 runbook §6 ④ 의 쿼리를 VM 에서
- [ ] **Step 6: 검증 6** — 전체 잡 수동 1회. 다음 날 `SELECT source_id, trigger, status, started_at FROM crawl_runs ORDER BY id DESC LIMIT 20` 에 due 행. Logging 에 `[refresh] 끝`
- [ ] **Step 7: 앱** — `/life/ask` 로 새 문서가 근거에 잡히는지 한 번
- [ ] **Step 8:** #325 본문 "확인한 것" 의 빈칸을 채운다 (`gh pr edit 325 --body-file`)

문제가 나면 코드 태스크로 돌아간다. 이 태스크는 며칠에 걸쳐 끝난다(Scheduler 는 다음 날).

---

### Task 9: 결정 기록과 문서

**Files:**
- Modify: `docs/decisions.md` (끝에 D-062)
- Modify: `docs/deploy/runbook.md` (§6 에 절 신설 + "Life 코퍼스만 동기화" 머리)
- Modify: `docs/deploy/roadmap.md` (§7-1 · §7-2 · §8)
- Modify: `docs/life/roadmap.md` (§0 · §4)
- Modify: `docker-compose.gcp.yml` (머리 주석)
- Modify: `CLAUDE.md` (규칙 절에 한 문단)
- Modify: `README.md` (루트, "크롤러 · 코퍼스")
- Modify: `docs/deploy/corpus-pipeline.md` (§3 가드 ② 문구를 구현과 맞춤 · `POSTGRES_HOST` → `POSTGRES_IP`)

- [ ] **Step 1: D-062**

`docs/decisions.md` 끝에 (D-060 형식을 따라):

```markdown
## D-062

**코퍼스 정본은 집 서버에 그대로 두고, GCP 는 별도 사본으로 크롤~적재를 사람 없이 돌린다 (2026-11-17 까지 실험).**

2026-09-08. #325 · `docs/deploy/corpus-pipeline.md`.

**배경** — 코퍼스 갱신이 개발 PC(파싱~적재)와 집 서버(크롤)에 묶여 있고, 그것을 GCP 에 반영하는 것은
`runbook.md` §6 의 `documents` 테이블 갈아끼우기 손작업(#290)이었다. roadmap §7-1 은 "정본을 VM 으로
옮기고 크롤러 컨테이너를 VM 에 띄운다" 였는데, 그러면 파싱 이후는 여전히 사람이 VM 에서 CLI 를 친다.

**결정**
1. **정본은 안 옮긴다.** GCP 코퍼스는 `raw/`+`crawl_log.jsonl` 초기 사본에서 갈라진 별도 코퍼스다.
   개정되는 원문이라 두 코퍼스는 합칠 수 없고(RAG-008·RAG-017), 그래서 11-17 삭제 때 잃을 것도
   되돌릴 것도 없다. 집 서버의 워커·Beat·코퍼스·§6 절차는 그대로다.
2. **Cloud Scheduler → Cloud Run Job `corpus-refresh`** 하나가 crawl → parse → chunk → embed → guard →
   load 를 한 프로세스에서 (`daengs_life/jobs/`). VM 에 Beat·크롤 워커를 띄우지 않는다. 코퍼스는 GCS
   버킷을 `/data` 로 마운트해 경로 코드를 안 고친다. DB 는 VM 의 pgvector 를 VPC 내부로 본다.
3. **적재 앞은 기계 가드만** — 행 수 급감(20%) · 파서 예외 · 메타 키 손실. 사람 승인 없음.
4. **GCP DB 의 `documents` 는 파이프라인만 쓴다.** §6 "Life 코퍼스만 동기화" 는 실험 기간 사용 금지.
5. 전체 재임베딩은 GPU 잡(Cloud Run Jobs L4, 싱가포르). 매일 증분은 CPU.
6. 관리자 콘솔 수동 크롤은 GCP 에서 Cloud Run Jobs API 로 (#326).

**되돌리기** — `infra/gcp/pipeline-teardown.sh`. 집 서버는 아무것도 안 바뀌었으므로 되돌릴 것이 없다.

**재개 조건** — 11-17 뒤에도 GCP 를 유지하기로 하면 "정본을 어디에 둘 것인가" 를 다시 결정한다.
그때 §7-1 의 원안(정본 이전)이 후보로 돌아온다.
```

- [ ] **Step 2: runbook §6**

"Life 코퍼스만 동기화 (GCP)" 절 제목 바로 아래에:

```markdown
> 🔴 **2026-09-08 부터 실험 기간(~11-17) 동안 이 절을 쓰지 마세요** (D-062). GCP 의 `documents` 는
> 이제 Cloud Run 잡 `corpus-refresh` 가 채웁니다. 이 절차로 갈아 끼우면 잡의 결과를 덮어씁니다.
> 아래 "코퍼스 파이프라인 (GCP)" 이 지금의 절차입니다. 이 절은 실험이 끝나 잡을 지운 뒤에만 다시 씁니다.
```

그 절 **앞에** 새 절 (§6 안, "점령 게임판 적재" 위):

```markdown
### 코퍼스 파이프라인 (GCP)

**D-062.** 설계 `corpus-pipeline.md`, 리소스 생성 `infra/gcp/README.md`. 매일 04:00 KST 에
Cloud Scheduler 가 잡 `corpus-refresh` 를 돌립니다. 손으로 할 일은 보통 없습니다.

**수동 실행**

```bash
gcloud run jobs execute corpus-refresh --region=asia-northeast3 --wait                       # 전체
gcloud run jobs execute corpus-refresh --region=asia-northeast3 --args="--stages,crawl,--sources,easylaw-pet" --wait
gcloud run jobs execute corpus-embed-full --region=asia-southeast1 --wait                    # 전체 재임베딩 (GPU, 모델·청커 교체 뒤)
```

**로그**

```bash
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="corpus-refresh"' \
  --limit=200 --format='value(textPayload)' | grep '\[refresh\]'
```

`[refresh] ▶ 단계` · `[refresh] load 가드 — …` · `[refresh] 끝` 을 찾으면 됩니다. 실패하면 Monitoring 이메일이 옵니다.

**되돌리기** — 잘못된 적재는 버킷 버전에서 이전 parquet 을 꺼내 다시 적재합니다:

```bash
gcloud storage ls -a gs://daengs-corpus/processed/embeddings/qwen3-embedding-0.6b.parquet   # 세대 목록
gcloud storage cp gs://daengs-corpus/processed/embeddings/qwen3-embedding-0.6b.parquet#<generation> \
  gs://daengs-corpus/processed/embeddings/qwen3-embedding-0.6b.parquet
gcloud run jobs execute corpus-refresh --region=asia-northeast3 --args="--stages,load" --wait
```

**가드에 걸렸을 때** — 로그의 이유를 보고 원인을 고친 뒤 다시 실행합니다. 정말 그 급감이 맞으면
`--args="--stages,load,--max-drop,0.9"` 처럼 한 번만 한계를 올립니다.
```

- [ ] **Step 3: roadmap §7-1 · §7-2 · §8**

§7 의 1번 항목을 통째로:

```markdown
1. **크롤러·코퍼스·적재 이전** — 🟢 **#325 · D-062 로 진행 중.** 2026-09-08 에 방향을 바꿨다:
   정본을 옮기지 않고 GCP 에 **별도 사본**을 두어 Cloud Scheduler → Cloud Run Job 이 크롤~적재를
   사람 없이 돌린다 (`corpus-pipeline.md`). 11-17 까지의 실험이고 그때 지운다. 그때까지 §6 의
   "Life 코퍼스만 동기화" 는 **쓰지 않는다** — 잡의 결과를 덮어쓴다.
```

2번 항목 끝에 한 문장: `**파이프라인 이미지는 #325 가 먼저 구웠다** (`docker/pipeline/Dockerfile`, Cloud Build + Artifact Registry). 서빙 컨테이너는 여전히 기동 때 sync.`

§8 체크리스트에 한 줄: `- [ ] 코퍼스 파이프라인 삭제 — `PROJECT=… bash infra/gcp/pipeline-teardown.sh` (버킷은 마지막에 따로)`

- [ ] **Step 4: life roadmap · compose 주석 · CLAUDE.md · README**

`docs/life/roadmap.md` — `grep -n "재적재\|GCP 동기화\|§6\|✔" docs/life/roadmap.md` 로 자리를 찾아, §4 의 해당 항목과 "재적재와 GCP 동기화" 절을 "GCP 는 `corpus-refresh` 잡이 자동(D-062). 집 서버는 종전대로 개발 PC 에서 `rag load`. 표의 ✔ 는 집 서버 기준" 으로. §0 요약의 문장도 같은 뜻으로.

`docker-compose.gcp.yml` 머리의 "크롤러는 일부러 목록에 없습니다 — 코퍼스 정본은 로컬 서버입니다" 문단을:

```
# 크롤러(crawler-worker · crawler-beat)는 **일부러 목록에 없습니다** — GCP 에서는 Cloud Run 잡
# `corpus-refresh` 가 크롤~적재를 대신합니다 (D-062, docs/deploy/corpus-pipeline.md). 코퍼스 정본은
# 여전히 로컬 서버입니다. 단 compose 는 파일 해석 시점에 `:?` 가드를 평가하므로, 안 띄워도
# 최상단 .env 의 DAENGS_CORPUS_DIR 에는 더미 경로가 있어야 합니다 (runbook §2).
```

`CLAUDE.md` "크롤은 서버가 합니다" 절 끝에:

```markdown
  **GCP 는 다릅니다** (D-062, 2026-09-08 ~ 11-17 실험). 거기서는 Cloud Run 잡 `corpus-refresh` 가
  크롤부터 적재까지 사람 없이 돌고, 코퍼스는 GCS 버킷 `daengs-corpus` 의 **별도 사본**입니다. 집
  서버 정본과는 갈라져 있고 합치지 않습니다. GCP 의 `documents` 를 손으로 갈아 끼우지 마세요.
  절차는 `docs/deploy/runbook.md` §6 "코퍼스 파이프라인 (GCP)".
```

루트 `README.md` "크롤러 · 코퍼스" 절 끝에 한 줄: `GCP 에서는 크롤~적재를 Cloud Run 잡이 자동으로 합니다 (D-062, `docs/deploy/corpus-pipeline.md`).`

`docs/deploy/corpus-pipeline.md` §3 — 가드 ② 를 "`rag parse` 가 예외 1건이면 종료 코드 1 이라 거기서 멈춘다" 로, `POSTGRES_HOST` 를 `POSTGRES_IP` 로 고친다 (rag 설정 필드명).

- [ ] **Step 5: 인용 자리 grep**

```bash
grep -rn "Life 코퍼스만 동기화\|7-1\|코퍼스 정본" docs CLAUDE.md README.md docker-compose.gcp.yml | grep -v corpus-pipeline
```

나온 자리마다 위 뜻과 어긋나는 문장이 없는지 읽고 고친다.

- [ ] **Step 6: 커밋**

```bash
git add docs/decisions.md docs/deploy docs/life/roadmap.md docker-compose.gcp.yml CLAUDE.md README.md
git commit -m "docs: D-062 — GCP 코퍼스 파이프라인, 정본은 집 서버 그대로"
```

---

### Task 10: 마무리

- [ ] **Step 1: 전체 테스트**

```bash
cd backend && uv run pytest -q 2>&1 | tail -5
```

Expected: 실패 0. (`ml`·`gait`·`screening` 이 없으면 그 테스트는 skip — CI 와 같다.)

- [ ] **Step 2: ruff**

```bash
cd backend && uv run ruff check src/daengs_life/jobs tests/test_jobs_*.py
```

- [ ] **Step 3: 계획 문서 처리**

이 파일(`docs/deploy/corpus-pipeline-plan.md`)은 구현 안내서라 머지에 남기지 않는다:

```bash
git rm docs/deploy/corpus-pipeline-plan.md
git commit -m "docs: 구현 계획 문서 제거 — 설계는 corpus-pipeline.md 에 남는다"
```

- [ ] **Step 4: PR #325 본문 갱신** — 작업 목록 체크, "확인한 것" 숫자, Task 8 에서 걸린 것을 컨텍스트 메모에. Draft 해제는 사람이.

---

## 자기 검토

- **스펙 커버리지** — §2 구성(Task 6·7) · §3 진입점·가드·동시 실행·이미지·env(Task 1~6) · §4 리소스 전부(Task 7 스크립트, Monitoring 은 CHANNEL 이 있을 때만) · §5 오류 처리(Task 5 종료 코드, Task 3 트랜잭션, Task 4 동시 실행, runbook 되돌리기) · §6 검증(Task 5 Step 5 · Task 6 Step 3 · Task 8) · §7 문서(Task 9). 관리자 트리거는 #326 이라 범위 밖.
- **스펙과 구현의 차이** — 가드 ② "파서 예외 상한·소스 0건" 을 "파서 예외 1건이면 중단" 으로 단순화했다 (Task 9 Step 4 가 스펙을 맞춘다). `POSTGRES_HOST` 는 rag 필드명이 `postgres_ip` 라 `POSTGRES_IP`.
- **이름 일관성** — `stages.run_crawl/run_parse/run_chunk/run_embed`, `jobs_load.run_load(dry_run=, max_drop=)`, `lock.from_env()`, `guard.check(before, planned, losing, max_drop=)`, `CrawlSummary(selected, ok, failed, unavailable)` — Task 2·3·4·5 가 같은 이름을 쓴다.
