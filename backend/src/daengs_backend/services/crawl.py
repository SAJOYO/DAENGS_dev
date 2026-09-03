"""크롤 수동 트리거와 실행 이력 조회 (RAG-001 요구사항 ②③ · RAG-047).

**`daengs_life` 를 import 하지 않습니다.** CLAUDE.md 가 "`daengs_backend` 가 `daengs_life` 를
부르는 접점은 `main.py` 의 세 줄뿐"이라고 못박아 두었고, 그 선을 태스크 하나 부르자고 넘으면
D-021 2단계(`/ask` 를 별도 프로세스로 떼기)가 그만큼 비싸집니다. 대신 브로커에 **태스크 이름
문자열**을 던집니다 — 워커 쪽이 `@app.task(name="daengs_life.tasks.crawl.crawl_due")` 로 그
이름을 명시하고 있어서 그 문자열이 계약입니다.

대가는 명확합니다: **이름이 틀려도 여기서는 안 잡힙니다.** 메시지는 정상으로 나가고 아무도 그것을
가져가지 않아 큐에 쌓입니다. 그래서 이름을 상수로 한 자리에 두고, 테스트가 워커 쪽 태스크 이름과
같은지 대조합니다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.config import settings
from daengs_backend.models.crawl_run import CrawlRun
from daengs_backend.repositories import crawl_run as repo

log = logging.getLogger(__name__)

#: 워커의 `@app.task(name=...)` 과 **문자 그대로 같아야** 합니다. 테스트가 대조합니다.
CRAWL_TASK = "daengs_life.tasks.crawl.crawl_due"

#: 워커가 듣고 있는 큐. `--queues crawl` 로 떠 있습니다 (RAG-044 ⑧).
#: 기본 `celery` 큐로 보내면 실시간 워커가 가져가려다 실패하거나, 아무도 안 가져갑니다.
CRAWL_QUEUE = "crawl"


class BrokerUnavailable(RuntimeError):
    """브로커 주소가 없거나 연결이 안 됨. 라우터가 503 으로 바꿉니다."""


def _celery():
    """**보내기 전용** Celery 앱. 태스크를 등록하지 않으므로 이 프로세스는 워커가 아닙니다."""
    if not settings.redis_url:
        raise BrokerUnavailable("REDIS_URL 이 없다 — backend/.env 를 확인할 것")
    from celery import Celery

    return Celery(broker=settings.redis_url)


def trigger(source_ids: Sequence[str] | None = None) -> str:
    """수동 트리거. 태스크 id 를 돌려줍니다.

    `source_ids` 가 없으면 due 판정을 태스크가 합니다 — **주기 실행과 같은 경로**입니다
    (RAG-001 원칙 4). 여기서 소스를 거르지 않는 것도 그쪽과 같습니다: 사람이 이름을 대고 부른
    것을 "cadence 가 manual 이라"며 막으면 manual 소스를 영영 못 받습니다 (RAG-044).

    **이 함수는 기다리지 않습니다.** 결과는 `crawl_runs` 에 남고 화면이 그것을 폴링합니다
    (RAG-001 원칙 6). 크롤 하나가 분 단위라 요청을 붙들고 있을 수 없습니다.
    """
    app = _celery()
    kwargs = {"source_ids": list(source_ids)} if source_ids else {}
    try:
        async_result = app.send_task(CRAWL_TASK, kwargs=kwargs, queue=CRAWL_QUEUE)
    except Exception as e:                      # noqa: BLE001 — 브로커가 죽은 것은 500 이 아니다
        raise BrokerUnavailable(f"브로커에 보내지 못했다: {type(e).__name__}: {e}") from e

    log.info("크롤 수동 트리거 — task=%s sources=%s", async_result.id, list(source_ids or []))
    return async_result.id


async def latest(session: AsyncSession) -> Sequence[CrawlRun]:
    """소스별 마지막 실행. 화면의 기본 목록입니다."""
    return await repo.latest_per_source(session)


async def history(session: AsyncSession, source_id: str, limit: int = 20) -> Sequence[CrawlRun]:
    return await repo.list_for_source(session, source_id, limit=limit)


async def running_count(session: AsyncSession) -> int:
    return await repo.count_running(session)


def crawl_workers(timeout_sec: float = 1.0) -> list[str]:
    """`crawl` 큐를 듣고 있는 워커 이름들. 없으면 빈 목록입니다 (#180 상태 페이지).

    **DB 만으로는 "이 환경에 크롤러가 있나"를 알 수 없습니다.** `crawl_runs` 에 행이 있다는
    것은 *언젠가* 돌았다는 뜻이지 지금 워커가 떠 있다는 뜻이 아닙니다 — GCP 는 09-02 로컬
    덤프를 그대로 쓰고 있어서 **행은 있는데 워커는 없습니다** (`docs/deploy/roadmap.md` §2-4).
    그 둘을 안 가르면 상태 화면이 GCP 에서 "크롤 정상" 이라고 거짓말합니다.

    그래서 브로커에 직접 묻습니다. `active_queues()` 는 살아 있는 워커에게 "무슨 큐를 듣고
    있나"를 브로드캐스트하고 `timeout_sec` 동안 답을 모읍니다. `ping()` 이 아니라 이것을
    쓰는 이유 — 이 브로커에는 실시간 워커도 붙어 있어서, ping 은 **크롤러가 아닌 워커의
    답**을 크롤러가 있다는 뜻으로 읽습니다.

    **동기입니다** (kombu). 부르는 쪽이 스레드로 돌립니다.

    브로커 주소가 없으면 `BrokerUnavailable` 입니다 — `trigger` 와 같은 규칙입니다.
    """
    app = _celery()
    try:
        replies = app.control.inspect(timeout=timeout_sec).active_queues()
    except Exception as e:                      # 브로커가 죽은 것은 500 이 아니다
        raise BrokerUnavailable(f"브로커에 묻지 못했다: {type(e).__name__}: {e}") from e

    # 아무도 답하지 않으면 None 입니다 (빈 dict 가 아닙니다).
    if not replies:
        return []
    return sorted(
        node
        for node, queues in replies.items()
        if any(q.get("name") == CRAWL_QUEUE for q in queues or [])
    )
