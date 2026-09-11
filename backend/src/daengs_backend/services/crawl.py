"""크롤 수동 트리거와 실행 이력 조회 (RAG-001 요구사항 ②③ · RAG-047).

**`daengs_life` 를 import 하지 않습니다.** CLAUDE.md 가 "`daengs_backend` 가 `daengs_life` 를
부르는 접점은 `main.py` 의 세 줄뿐"이라고 못박아 두었고, 그 선을 태스크 하나 부르자고 넘으면
D-021 2단계(`/life/ask` 를 별도 프로세스로 떼기)가 그만큼 비싸집니다. 대신 브로커에 **태스크 이름
문자열**을 던집니다 — 워커 쪽이 `@app.task(name="daengs_life.tasks.crawl.crawl_due")` 로 그
이름을 명시하고 있어서 그 문자열이 계약입니다.

대가는 명확합니다: **이름이 틀려도 여기서는 안 잡힙니다.** 메시지는 정상으로 나가고 아무도 그것을
가져가지 않아 큐에 쌓입니다. 그래서 이름을 상수로 한 자리에 두고, 테스트가 워커 쪽 태스크 이름과
같은지 대조합니다.

GCP 에서는 브로커 대신 **Cloud Run Job** 이다 (#326, D-062 §3). `settings.crawl_backend` 가
`cloudrun` 이면 `services/cloudrun_jobs.py` 로 `corpus-refresh` 를 실행한다 — 집 서버의 Celery 는
크롤에서 멈추지만 이 잡은 적재까지 간다. 끝나지 않은 실행이 있으면 새로 띄우지 않고
`AlreadyRunning` 으로 그 이름을 돌려준다(잡 쪽도 같은 확인을 하지만, 버튼을 누른 사람에게는
여기서 바로 알려 주는 편이 낫다).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.config import settings
from daengs_backend.models.crawl_run import CrawlRun
from daengs_backend.repositories import crawl_run as repo
from daengs_backend.services import cloudrun_jobs

log = logging.getLogger(__name__)

#: 워커의 `@app.task(name=...)` 과 **문자 그대로 같아야** 합니다. 테스트가 대조합니다.
CRAWL_TASK = "daengs_life.tasks.crawl.crawl_due"

#: 워커가 듣고 있는 큐. `--queues crawl` 로 떠 있습니다 (RAG-044 ⑧).
#: 기본 `celery` 큐로 보내면 실시간 워커가 가져가려다 실패하거나, 아무도 안 가져갑니다.
CRAWL_QUEUE = "crawl"

#: Cloud Run API 를 부를 때 주는 시간. 트리거는 사람이 버튼을 누르고 기다리는 요청이라
#: 상태 페이지의 `WORKER_PING_SEC`(1초, 항목 전체 예산은 `ITEM_TIMEOUT_SEC` 2초)보다 넉넉하게
#: 잡는다 — 그래도 무한은 아니라서 API 가 죽으면 요청 스레드가 영영 물려 있지는 않는다
#: (#326 리뷰 라운드 1).
CLOUDRUN_TRIGGER_TIMEOUT_SEC = 30.0


class BrokerUnavailable(RuntimeError):
    """브로커 주소가 없거나 연결이 안 됨. 라우터가 503 으로 바꿉니다."""


class AlreadyRunning(RuntimeError):
    """GCP 에서 끝나지 않은 실행이 있어 새로 띄우지 않았다. `execution` 이 그 이름이다."""

    def __init__(self, execution: str) -> None:
        super().__init__(execution)
        self.execution = execution


def _celery():
    """**보내기 전용** Celery 앱. 태스크를 등록하지 않으므로 이 프로세스는 워커가 아닙니다."""
    if not settings.redis_url:
        raise BrokerUnavailable("REDIS_URL 이 없다 — backend/.env 를 확인할 것")
    from celery import Celery

    return Celery(broker=settings.redis_url)


def trigger(source_ids: Sequence[str] | None = None) -> str:
    """수동 트리거. 태스크 id(또는 Cloud Run 실행 이름)를 돌려줍니다.

    `source_ids` 가 없으면 due 판정을 태스크가 합니다 — **주기 실행과 같은 경로**입니다
    (RAG-001 원칙 4). 여기서 소스를 거르지 않는 것도 그쪽과 같습니다: 사람이 이름을 대고 부른
    것을 "cadence 가 manual 이라"며 막으면 manual 소스를 영영 못 받습니다 (RAG-044).

    **이 함수는 기다리지 않습니다.** 결과는 `crawl_runs` 에 남고 화면이 그것을 폴링합니다
    (RAG-001 원칙 6). 크롤 하나가 분 단위라 요청을 붙들고 있을 수 없습니다.
    """
    if settings.crawl_backend == "cloudrun":
        return _trigger_cloudrun(source_ids)
    return _trigger_celery(source_ids)


def _trigger_celery(source_ids: Sequence[str] | None) -> str:
    app = _celery()
    kwargs = {"source_ids": list(source_ids)} if source_ids else {}
    try:
        async_result = app.send_task(CRAWL_TASK, kwargs=kwargs, queue=CRAWL_QUEUE)
    except Exception as e:                      # noqa: BLE001 — 브로커가 죽은 것은 500 이 아니다
        raise BrokerUnavailable(f"브로커에 보내지 못했다: {type(e).__name__}: {e}") from e

    log.info("크롤 수동 트리거 — task=%s sources=%s", async_result.id, list(source_ids or []))
    return async_result.id


def _trigger_cloudrun(source_ids: Sequence[str] | None) -> str:
    """Cloud Run Job `corpus-refresh` 를 실행한다. 소스가 있으면 `--sources a b` 를 컨테이너 인자로.

    **크롤만이 아니라 적재까지 간다** — 그 잡의 뜻이 그렇다(D-062). 끝나지 않은 실행이 있으면
    `AlreadyRunning`. API 오류는 `BrokerUnavailable` — 라우터에겐 "실행기가 없다" 와 같은 503 이다.
    """
    if not settings.gcp_project:
        raise BrokerUnavailable("DAENGS_GCP_PROJECT 가 없다 — backend/.env 를 확인할 것")
    project, region, job = settings.gcp_project, settings.gcp_region, settings.corpus_job
    try:
        active = cloudrun_jobs.active_execution(
            project, region, job, timeout=CLOUDRUN_TRIGGER_TIMEOUT_SEC)
        if active:
            raise AlreadyRunning(active)
        args = ["--sources", *source_ids] if source_ids else []
        execution = cloudrun_jobs.run(
            project, region, job, args, timeout=CLOUDRUN_TRIGGER_TIMEOUT_SEC)
    except AlreadyRunning:
        raise
    except Exception as e:                      # API 가 죽은 것은 500 이 아니다
        raise BrokerUnavailable(f"Cloud Run 에 보내지 못했다: {type(e).__name__}: {e}") from e
    log.info("크롤 수동 트리거(cloudrun) — execution=%s sources=%s", execution, list(source_ids or []))
    return execution


async def latest(session: AsyncSession) -> Sequence[CrawlRun]:
    """소스별 마지막 실행. 화면의 기본 목록입니다."""
    return await repo.latest_per_source(session)


async def history(session: AsyncSession, source_id: str, limit: int = 20) -> Sequence[CrawlRun]:
    return await repo.list_for_source(session, source_id, limit=limit)


async def running_count(session: AsyncSession) -> int:
    """안 끝난 실행 **전부.** `CrawlStatusOut.running` 이 이 값입니다 — 관리자 화면이 폴링하는
    계약이라 뜻을 안 바꿉니다. 도는 중과 죽어 남은 것을 갈라야 하면 아래 `running_split`."""
    return await repo.count_running(session)


#: `running` 이 이보다 오래됐으면 **워커가 죽어 남은 것**으로 봅니다.
#:
#: 잔존 행 자체는 버그가 아닙니다 — `daengs_life/tasks/crawl_runs.py` 의 `start()` 가
#: *"워커가 중간에 죽으면 이 행이 `running` 으로 남는데, 그것이 정보다"* 라고 적어 뒀고,
#: 기록이 크롤을 죽이지 않는다는 계약 때문에 `finish()` 를 못 부르고 죽는 경로가 **항상
#: 열려 있습니다.** 그래서 고칠 것은 쓰는 쪽이 아니라 **읽는 쪽**이고, 그 판단이 이 상수입니다.
#:
#: 6시간인 근거는 위아래 두 벽입니다.
#:   · **아래** — 소스 하나의 수집은 요청 간격 1~2초의 네트워크 I/O 라 **길어야 분 단위**입니다.
#:   · **위**   — Beat 가 **하루 한 번 KST 04:00** 에 돕니다 (RAG-050). 24시간을 넘기면
#:                "어제 죽어 남은 행"과 "오늘 도는 행"이 겹쳐 **애초에 못 가릅니다.**
#: 늘리면 죽은 워커를 늦게 알아채고, 줄이면 느린 수집을 죽었다고 오해합니다.
#: ⚠ **크롤 주기가 하루보다 촘촘해지면 이 값을 다시 봐야 합니다.**
RUNNING_STALE_AFTER = timedelta(hours=6)


async def running_split(session: AsyncSession) -> tuple[int, int]:
    """안 끝난 실행을 `(도는 중, 죽어 남은 것)` 으로 가릅니다.

    **자르는 시각을 여기서 계산해 repositories 에 넘깁니다** — 저쪽은 쿼리만 있고 판단이 없고
    (`repositories/crawl_run.py` 머리), "얼마나 오래면 죽은 것인가"는 판단이라 services 몫입니다
    (D-011 의 계층 구분).

    합은 `running_count()` 와 같습니다. 두 질의를 따로 던지는 것은 `COUNT` 두 번이 인덱스
    스캔이라 싸고, 한 질의에 `FILTER` 로 묶으면 repositories 가 임계값을 알아야 해서입니다.
    """
    stale = await repo.count_running(
        session, started_before=datetime.now(UTC) - RUNNING_STALE_AFTER)
    return await repo.count_running(session) - stale, stale


def crawl_workers(timeout_sec: float = 1.0) -> list[str]:
    """크롤러가 지금 떠 있다는 신호. 없으면 빈 목록입니다 (#180 상태 페이지).

    **DB 만으로는 "이 환경에 크롤러가 있나"를 알 수 없습니다.** `crawl_runs` 에 행이 있다는
    것은 *언젠가* 돌았다는 뜻이지 지금도 있다는 뜻이 아닙니다 — 예를 들어 09-02 GCP 는 로컬
    덤프를 그대로 쓰고 있어서 한동안 **행은 있는데 크롤러는 없었습니다**
    (`docs/deploy/roadmap.md` §2-4). 그 둘을 안 가르면 상태 화면이 거짓말을 합니다.

    **`crawl_backend` 에 따라 갈립니다** (#326).

    - **celery**: 브로커에 직접 묻습니다. `active_queues()` 는 살아 있는 워커에게 "무슨 큐를
      듣고 있나"를 브로드캐스트하고 `timeout_sec` 동안 답을 모읍니다. `ping()` 이 아니라
      이것을 쓰는 이유 — 이 브로커에는 실시간 워커도 붙어 있어서, ping 은 **크롤러가 아닌
      워커의 답**을 크롤러가 있다는 뜻으로 읽습니다. 브로커 주소가 없으면
      `BrokerUnavailable` 입니다 — `trigger` 와 같은 규칙입니다.
    - **cloudrun**: 워커라는 것이 없으므로 `job_exists` 로 잡 자체가 있는지만 봅니다 —
      있으면 `[f"{job}@{region}"]` 하나, 없으면 빈 목록입니다. `gcp_project` 가 없거나 API 가
      죽으면 마찬가지로 `BrokerUnavailable` 입니다.

    **동기입니다** (kombu · google-cloud-run 클라이언트 둘 다). 부르는 쪽이 스레드로 돌립니다.
    """
    if settings.crawl_backend == "cloudrun":
        if not settings.gcp_project:
            raise BrokerUnavailable("DAENGS_GCP_PROJECT 가 없다")
        try:
            exists = cloudrun_jobs.job_exists(
                settings.gcp_project, settings.gcp_region, settings.corpus_job,
                timeout=timeout_sec)
        except Exception as e:                  # API 가 죽은 것은 500 이 아니다
            raise BrokerUnavailable(f"Cloud Run 에 묻지 못했다: {type(e).__name__}: {e}") from e
        return [f"{settings.corpus_job}@{settings.gcp_region}"] if exists else []

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
