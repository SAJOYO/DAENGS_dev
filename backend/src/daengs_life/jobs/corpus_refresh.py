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

    try:
        other = lock.from_env()
    except Exception as e:                      # noqa: BLE001 — 잠금 확인 실패가 잡을 죽이면 안 된다
        log.warning("[refresh] 동시 실행 확인 실패 — 그대로 진행한다: %s: %s", type(e).__name__, e)
        other = None
    if other:
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
