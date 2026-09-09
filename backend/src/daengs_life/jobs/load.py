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
