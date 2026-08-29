"""크롤 태스크 — **due 소스 선별 하나뿐** (RAG-001 원칙 4 · RAG-044).

여기 있는 것은 "무엇을 부를지 고르고 결과를 세는 것"뿐이다. 주기 판정은
`crawler.core.cadence`, 수집은 `crawler.run` 에 있고 **CLI 가 타는 것과 같은 경로**다
(`tasks/realtime.py` 와 같은 배치 — 전용 경로를 따로 두면 새 소스가 CLI 에서만 되고 Beat 에서는
조용히 안 되는 날이 온다).

**태스크가 하나인 것은 원칙 4 그대로다.** 소스마다 별도 태스크로 fan-out 하면 원칙 5 의
`autoretry_for` 를 소스 단위로 걸 수 있어 더 낫지만, 그건 실행 이력 테이블(원칙 3)이 생긴 뒤에
같이 하는 것이 맞다 — 지금은 재시도가 남긴 것을 볼 곳이 `crawl_log.jsonl` 뿐이라 워커 안에서
몇 번을 돌았는지 아무도 모른다. 대신 **한 소스가 죽어도 나머지는 계속 받는다** (원칙 5 의 절반).

⚠ **적재로 이어 붙이지 않는다** (카드 메모 ③ · RAG-002 · RAG-025). 바뀐 것이 있으면 경고 한 줄을
남기고 멈춘다. `parse → chunk → embed → load` 는 GPU 와 검문소가 걸려 있어 사람이 랩을 뜨고
판단하는 자리다. 그 경고가 C3(법령·약관 개정 감지)의 입력이 된다.
"""
from __future__ import annotations

import logging
from datetime import datetime

from daengs_life.crawler import run as crawler_run
from daengs_life.crawler.core import cadence, registry
from daengs_life.crawler.core.config import KST

from .celery_app import app

log = logging.getLogger(__name__)


@app.task(name="daengs_life.tasks.crawl.crawl_due")
def crawl_due(source_ids: list[str] | None = None) -> dict[str, object]:
    """due 인 소스를 순서대로 수집한다.

    `source_ids` 가 오면 **due 판정을 건너뛰고 그것만** 받는다 — 관리자페이지의 수동 트리거가
    타는 경로이고(RAG-001 요구사항 ②③), 주기 실행과 **같은 함수**를 지나간다. 거르지도 않는다:
    사람이 이름을 대고 부른 것을 "cadence 가 manual 이라"며 안 받으면 manual 소스를 영영 못 받는다.
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

    results: dict[str, dict[str, object]] = {}
    changed_docs: dict[str, list[str]] = {}

    for source_id in selected:
        try:
            result = crawler_run.run(source_id)
        except Exception as e:                  # noqa: BLE001 — 한 소스가 죽어도 나머지는 받는다
            log.exception("소스 %s 수집 실패", source_id)
            results[source_id] = {"error": f"{type(e).__name__}: {e}"}
            continue

        if result.unavailable:
            # 키 미설정·시드 URL 사망. 실패가 아니라 **아직 못 하는 것**이라 사람이 고쳐야 한다.
            log.warning("소스 %s 수집 불가 — %s", source_id, result.unavailable)
            results[source_id] = {"unavailable": result.unavailable}
            continue

        results[source_id] = {
            "fetched": result.fetched,
            "changed": result.changed,
            "failed": result.failed,
            "skipped": result.skipped,
            "run_id": result.run_id,
        }
        if result.changed_slugs:
            changed_docs[source_id] = result.changed_slugs

    if changed_docs:
        # **여기서 멈춘다.** 이 줄이 C3 의 입력이다 (메모 ③).
        total = sum(len(v) for v in changed_docs.values())
        log.warning("바뀐 문서 %d건 — 적재는 사람이 판단한다 (RAG-002 · RAG-025): %s",
                    total, {k: len(v) for k, v in changed_docs.items()})

    return {
        "mode": mode,
        "selected": selected,
        "results": results,
        "changed_docs": changed_docs,
        "ran_at": now.isoformat(timespec="seconds"),
    }


__all__ = ["crawl_due"]
