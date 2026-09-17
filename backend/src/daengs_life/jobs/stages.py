"""단계 하나씩을 부르는 함수들. **로직은 여기 없다** — `crawler.run` 과 `rag/__main__.py` 의
`cmd_*` 를 그대로 부른다 (D-062). `cmd_*` 가 argparse Namespace 를 받으므로 여기서 같은 모양을
만들어 준다. 그 함수의 인자가 늘면 여기 Namespace 도 같이 는다 — 어긋나면 AttributeError 로
바로 터지지 조용히 다른 값을 쓰지 않는다.

크롤은 `tasks/crawl.py` 의 `crawl_source` 와 같은 흐름이되 Celery 재시도가 없다. 소스 하나가
죽어도 다음 소스로 가고, 시도마다 `crawl_runs` 에 한 행을 남기는 것은 그쪽과 같다 (RAG-047).
due 뒤의 법령 개정 조회도 `tasks/crawl.py` 의 `crawl_due` / `_revised_sources` 를 그대로 옮긴
것이다 (RAG-087) — D-062 로 이 파일을 새로 짤 때 그 조회가 빠져 GCP 에서는 법령이 다시
수집되지 않았다.
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from datetime import datetime

from daengs_life.crawler import run as crawler_run
from daengs_life.crawler.core import cadence, registry, revision
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
    revised: list[str] = field(default_factory=list)
    ok: int = 0
    failed: int = 0
    unavailable: int = 0


def run_crawl(source_ids: list[str] | None, *, dry_run: bool) -> CrawlSummary:
    """due 소스(또는 지정 소스)를 순서대로 받는다. 예외는 소스 단위로 삼킨다.

    `source_ids` 가 없을 때(due 모드)만 법령 개정 조회(`_revised_sources`)를 덧붙인다 —
    수동 지정은 사람이 이름을 댄 것이라 판정을 또 걸 이유가 없다 (`tasks/crawl.py` 의
    `crawl_due` 와 같은 판정, RAG-087). **`dry_run` 이어도 조회는 한다** — `probe_sources` 는
    `discover()` 만 부르는 읽기 요청이고, dry-run 은 "무엇을 할지 미리 보기"이지 조회까지
    건너뛰는 모드가 아니다.
    """
    seeds = registry.load_seeds()
    revised: list[str] = []
    if source_ids:
        selected, trigger = list(source_ids), "manual"
        pairs = [(sid, trigger) for sid in selected]
    else:
        implemented = {sid for sid, seed in seeds.items() if registry.resolve(seed) is not None}
        selected = cadence.due_sources(seeds, implemented=implemented, now=datetime.now(KST))
        trigger = "due"
        revised = _revised_sources(seeds)
        due_set = set(selected)
        # due 에 이미 있는 소스가 개정으로도 잡히면 한 번만, trigger 는 due 로 (중복 수집 방지).
        pairs = [(sid, "due") for sid in selected] + [
            (sid, "revision") for sid in revised if sid not in due_set
        ]
    run_ids = [sid for sid, _ in pairs]
    revision_count = sum(1 for _, trig in pairs if trig == "revision")
    log.info("[refresh] crawl 대상 %d개 (%s, 개정 %d): %s",
             len(run_ids), trigger, revision_count, ", ".join(run_ids) or "없음")

    summary = CrawlSummary(selected=run_ids, revised=revised)
    for source_id, trig in pairs:
        row_id = None if dry_run else crawl_runs.start(source_id, trig)
        try:
            result = crawler_run.run(source_id, dry_run=dry_run)
        except Exception as e:
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


def _revised_sources(seeds) -> list[str]:
    """`tasks/crawl.py` 의 `_revised_sources` 와 같은 판정 — 로그에 `[refresh]` 접두만 붙였다.

    조회 전체가 죽어도 due 발사는 막지 않는다 (RAG-087)."""
    try:
        verdicts = revision.probe_sources(seeds)
    except Exception as e:                      # noqa: BLE001 — 조회 실패가 due 를 막으면 안 된다
        log.warning("[refresh] 개정 조회 전체 실패 — 오늘은 due 만 받는다: %s", e)
        return []
    out: list[str] = []
    for sid, vs in verdicts.items():
        hits = [v for v in vs if v.kind in ("new", "revised")]
        if hits:
            out.append(sid)
            for v in hits:
                log.warning("[refresh] 개정 판정: %s / %s — %s (%s → %s)",
                            sid, v.slug, v.kind, v.previous, v.current)
        else:
            log.info("[refresh] 개정 조회: %s — 변화 없음 (%d건)", sid, len(vs))
    return out


def run_parse(*, dry_run: bool) -> int:
    return rag_cli.cmd_parse(argparse.Namespace(
        source=None, limit=0, force=False, dry_run=dry_run, verbose=False))


def run_chunk(*, dry_run: bool) -> int:
    return rag_cli.cmd_chunk(argparse.Namespace(
        source=None, force=False, dry_run=dry_run, verbose=False))


def run_embed(*, full: bool, dry_run: bool) -> int:
    """서빙 모델 하나만. `--full` 은 증분을 버리고 전량 (RAG-064)."""
    try:
        # 함수 안에서 임포트한다 — torch 는 `ml` 그룹에만 있고 base 의존성이 아니다.
        # 로그용: 잡이 어느 장치로 돌았는지 로그에 남긴다 (2026-09-08 GPU 잡이 CPU 로 돈 것을
        # 로그로 못 봤다). PLC0415(함수 내부 import) 는 이 저장소 ruff 설정에 없어 noqa 불필요.
        import torch
        log.info("[refresh] embed device — cuda_available=%s torch=%s",
                  torch.cuda.is_available(), torch.__version__)
    except ImportError:
        log.info("[refresh] embed device — torch 없음")
    return rag_cli.cmd_embed(argparse.Namespace(
        model=rag_config.settings.embedding_model_key, all_models=False, batch=8,
        force=False, guard_only=False, dry_run=dry_run, quiet=True, full=full,
        backfill_hashes=False, restamp=False))


__all__ = ["ORDER", "CrawlSummary", "parse_stages", "run_chunk", "run_crawl", "run_embed", "run_parse"]
