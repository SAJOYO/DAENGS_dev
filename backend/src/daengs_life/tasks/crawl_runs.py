"""크롤 실행 이력을 `crawl_runs` 에 남긴다 (RAG-001 원칙 3 · RAG-047).

**여기가 DB 를 아는 유일한 크롤 쪽 모듈이다.** `crawler` 패키지는 DB 를 모른 채로 둔다 —
`python -m crawler` 가 DB 없이 돌아야 하고(RAG-001 원칙 1), due 판정은 계속
`data/manifests/crawl_log.jsonl` 이 한다(RAG-044 ③). 이 테이블은 **그 로그 위의 요약**이고,
`run_id` 가 둘을 잇는 다리다.

**계약 하나: 기록이 크롤을 죽이지 않는다.** 모든 함수가 실패를 삼키고 로그 한 줄만 남긴다.
DB 가 없다고 수집이 멈추면 안 되기 때문이다 — 지금 `crawl_due` 는 한 소스가 죽어도 나머지를
계속 받는데(RAG-001 원칙 5의 절반), 여기에 죽는 경로를 새로 만들면 그 성질이 사라진다.
그래서 `start()` 는 실패하면 `None` 을 돌려주고, `finish()` 는 `None` 을 받으면 아무것도 안 한다.
**이력이 비는 것과 크롤이 안 도는 것 중에는 앞이 낫다.**
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _connect():
    """psycopg 연결 하나. `rag.stages.load.connect` 를 쓰지 않는 이유는 무게다 —
    저쪽은 pgvector 어댑터를 등록하는데 `crawl_runs` 에는 벡터 컬럼이 없고, 크롤 워커가
    그 의존성을 끌고 다닐 이유도 없다. 접속 정보는 같은 자리(`rag.core.config`)에서 받는다.
    """
    import psycopg

    from daengs_life.rag.core import config

    return psycopg.connect(config.settings.dsn, connect_timeout=5)


def start(source_id: str, trigger: str) -> int | None:
    """수집 시작을 남기고 행 id 를 돌려준다. 실패하면 `None`.

    `run_id` 는 아직 없다 — 수집이 끝나야 나온다. 그래서 `status='running'` 으로 넣고
    `finish()` 가 채운다. **워커가 중간에 죽으면 이 행이 `running` 으로 남는데, 그것이 정보다.**
    지금은 그렇게 죽으면 아무 흔적도 없다.
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO crawl_runs (source_id, trigger, status) "
                "VALUES (%s, %s, 'running') RETURNING id",
                (source_id, trigger),
            )
            row_id = cur.fetchone()[0]
            conn.commit()
            return row_id
    except Exception as e:                      # noqa: BLE001 — 계약: 기록은 크롤을 안 죽인다
        log.warning("crawl_runs 시작 기록 실패 (%s) — 수집은 계속한다: %s", source_id, e)
        return None


def finish(row_id: int | None, status: str, *,
           run_id: str | None = None,
           counts: dict[str, Any] | None = None,
           changed_slugs: list[str] | None = None,
           error: str | None = None) -> None:
    """수집 결과로 행을 닫는다. `row_id` 가 `None` 이면(=`start()` 가 실패했으면) 아무것도 안 한다."""
    if row_id is None:
        return
    c = counts or {}
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE crawl_runs SET status = %s, run_id = %s, "
                "docs_fetched = %s, docs_changed = %s, docs_failed = %s, docs_skipped = %s, "
                "changed_slugs = %s, error = %s, finished_at = NOW() WHERE id = %s",
                (status, run_id,
                 int(c.get("fetched", 0)), int(c.get("changed", 0)),
                 int(c.get("failed", 0)), int(c.get("skipped", 0)),
                 changed_slugs or [], error, row_id),
            )
            conn.commit()
    except Exception as e:                      # noqa: BLE001 — 위와 같은 계약
        log.warning("crawl_runs 종료 기록 실패 (id=%s) — 수집 결과는 로그에 있다: %s", row_id, e)


__all__ = ["start", "finish"]
