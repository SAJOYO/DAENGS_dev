"""크롤 태스크 — **due 소스 선별 하나뿐** (RAG-001 원칙 4 · RAG-044).

여기 있는 것은 "무엇을 부를지 고르고 결과를 세는 것"뿐이다. 주기 판정은
`crawler.core.cadence`, 수집은 `crawler.run` 에 있고 **CLI 가 타는 것과 같은 경로**다
(`tasks/realtime.py` 와 같은 배치 — 전용 경로를 따로 두면 새 소스가 CLI 에서만 되고 Beat 에서는
조용히 안 되는 날이 온다).

**태스크가 둘이다** — 고르는 `crawl_due` 와 하나를 받는 `crawl_source`. Beat 가 등록하는 것은
여전히 앞의 하나뿐이라 원칙 4 는 그대로다. 쪼갠 것은 RAG-047 에서 실행 이력 테이블이 생겼기
때문이다 — 재시도를 소스 단위로 걸 수 있게 됐고(원칙 5), 그 재시도가 남긴 것을 볼 곳이 생겼다.

⚠ **쪼갠 이유는 병렬성이 아니다.** 워커는 `--concurrency 1 --queues crawl` 그대로이고 태스크들은
순서대로 돈다. 동시성을 올리면 같은 호스트로 요청이 겹쳐 나가 `request_delay_sec` 1.5초가
무의미해진다 (`docs/data-sources.md` §12).

⚠ **적재로 이어 붙이지 않는다** (카드 메모 ③ · RAG-002 · RAG-025). 바뀐 것이 있으면 경고 한 줄을
남기고 멈춘다. `parse → chunk → embed → load` 는 GPU 와 검문소가 걸려 있어 사람이 랩을 뜨고
판단하는 자리다. 그 경고가 C3(법령·약관 개정 감지)의 입력이 된다.

**실행 이력은 `crawl_runs` 로 간다** (RAG-047). 예전에는 반환값 dict 로만 남았는데
`task_ignore_result=True` 라 **아무 데도 안 남았다** — 워커 로그를 사람이 읽지 않으면
무엇이 언제 돌았는지 알 길이 없었다. 기록은 `tasks/crawl_runs.py` 가 하고,
`crawler` 는 여전히 DB 를 모른다.
"""
from __future__ import annotations

import logging
from datetime import datetime

from daengs_life.crawler import run as crawler_run
from daengs_life.crawler.core import cadence, registry
from daengs_life.crawler.core.config import KST

from . import crawl_runs
from .celery_app import app

#: 크롤 워커가 듣고 있는 큐. `daengs_backend.services.crawl.CRAWL_QUEUE` 와 같아야 한다.
QUEUE = "crawl"

log = logging.getLogger(__name__)


@app.task(
    name="daengs_life.tasks.crawl.crawl_source",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,             # 지수 백오프 (RAG-001 원칙 5)
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def crawl_source(self, source_id: str, trigger: str = "due") -> dict[str, object]:
    """소스 **하나**를 수집한다. 재시도가 걸리는 자리다 (RAG-001 원칙 5).

    `crawl_due` 에서 떼어낸 이유는 재시도와 이력이지 **병렬성이 아니다.** 워커는 여전히
    `--concurrency 1 --queues crawl` 로 뜨고, 그래서 이 태스크들은 순서대로 돈다.
    동시성을 올리면 같은 호스트로 요청이 겹쳐 나가 `request_delay_sec` 1.5초가 무의미해지고,
    그건 크롤 예절 위반이다 (`docs/data-sources.md` §12). **쪼갠 이유가 코드에 안 보이므로
    여기 적어 둔다** (RAG-047).

    **재시도는 시도마다 `crawl_runs` 에 한 행을 남긴다.** 합치지 않는 것이 의도다 — 세 번
    실패하고 네 번째에 성공한 것과 한 번에 성공한 것은 다른 사건이고, 화면에서 그게 보여야
    "이 소스가 요즘 불안하다"를 사람이 안다.

    `unavailable`(키 미설정·시드 URL 사망)은 **재시도하지 않는다.** 다시 걸어도 같은 결과이고
    사람이 고쳐야 하는 것이라, 예외가 아니라 정상 반환으로 끝낸다.
    """
    row_id = crawl_runs.start(source_id, trigger)
    try:
        result = crawler_run.run(source_id)
    except Exception as e:                      # noqa: BLE001 — autoretry_for 가 다시 부른다
        crawl_runs.finish(row_id, "failed",
                          error=f"{type(e).__name__}: {e} (시도 {self.request.retries + 1})")
        raise

    if result.unavailable:
        log.warning("소스 %s 수집 불가 — %s", source_id, result.unavailable)
        crawl_runs.finish(row_id, "unavailable", error=result.unavailable)
        return {"source_id": source_id, "unavailable": result.unavailable}

    counts = {"fetched": result.fetched, "changed": result.changed,
              "failed": result.failed, "skipped": result.skipped}
    crawl_runs.finish(row_id, "ok", run_id=result.run_id, counts=counts,
                      changed_slugs=result.changed_slugs)

    if result.changed_slugs:
        # **여기서 멈춘다.** 이 줄이 C3 의 입력이다 (메모 ③). 적재로 이어 붙이지 않는다 —
        # `parse → chunk → embed → load` 는 GPU 와 검문소가 걸려 사람이 판단하는 자리다.
        log.warning("소스 %s 에서 바뀐 문서 %d건 — 적재는 사람이 판단한다 (RAG-002 · RAG-025)",
                    source_id, len(result.changed_slugs))

    return {"source_id": source_id, **counts, "run_id": result.run_id,
            "changed_slugs": result.changed_slugs}


@app.task(name="daengs_life.tasks.crawl.crawl_due")
def crawl_due(source_ids: list[str] | None = None) -> dict[str, object]:
    """무엇을 돌릴지 **고르기만** 한다. 수집은 소스별 `crawl_source` 가 한다.

    `source_ids` 가 오면 **due 판정을 건너뛰고 그것만** 받는다 — 관리자페이지의 수동 트리거가
    타는 경로이고(RAG-001 요구사항 ②③), 주기 실행과 **같은 함수**를 지나간다. 거르지도 않는다:
    사람이 이름을 대고 부른 것을 "cadence 가 manual 이라"며 안 받으면 manual 소스를 영영 못 받는다.

    **결과를 모아 돌려주지 않는다.** 예전에는 dict 로 모았는데 `task_ignore_result=True` 라
    아무 데도 안 남았다. 이제 결과가 갈 곳은 `crawl_runs` 이고(RAG-047), 여기서는 무엇을
    보냈는지만 돌려준다.
    """
    seeds = registry.load_seeds()
    now = datetime.now(KST)

    if source_ids:
        selected, mode = list(source_ids), "manual"
    else:
        implemented = {sid for sid, seed in seeds.items() if registry.resolve(seed) is not None}
        selected = cadence.due_sources(seeds, implemented=implemented, now=now)
        mode = "due"
        log.info("due 소스 %d개 / 시드 %d개 — %s", len(selected), len(seeds), ", ".join(selected) or "없음")

    dispatched: list[str] = []
    for source_id in selected:
        # 한 소스를 못 보내도 나머지는 보낸다 (원칙 5 의 절반. 이제는 발사 단계에서 지킨다).
        try:
            dispatched.append(crawl_source.apply_async(
                args=(source_id, mode), queue=QUEUE).id)
        except Exception as e:                  # noqa: BLE001
            log.exception("소스 %s 발사 실패", source_id)
            dispatched.append(f"(발사 실패: {type(e).__name__}: {e})")

    return {
        "mode": mode,
        "selected": selected,
        "dispatched": dispatched,
        "ran_at": now.isoformat(timespec="seconds"),
    }


__all__ = ["crawl_due", "crawl_source"]
