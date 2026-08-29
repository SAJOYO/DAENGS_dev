"""Celery 앱 — RAG-001 이 확정한 오케스트레이션의 **첫 실물** (RT-002 ②-b).

RAG-001 은 "Celery + Beat + Redis" 를 2026-08-19 에 확정했지만 그동안 선언만 있었다. 실제로
쓰는 첫 자리가 RT-001 ④-d 의 프리페치다 — 요청이 캐시를 채우고(single-flight), Beat 가
**활성 키만** 미리 데운다.

**의존 방향은 `tasks → realtime` 한쪽뿐이다** (RAG-009). 반대로 `realtime` 이 Celery 를 알면
`python -m realtime walk` 가 브로커 없이는 안 돌고, 그건 RAG-001 원칙 1(크롤러·엔진은 단독
실행된다)을 파트②에서 깨는 것이다. 그래서 프리페치 로직은 `realtime.collect.warm` 에 있고
여기 있는 것은 **스케줄과 예산 판단뿐**이다.
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from daengs_life.realtime.config import REDIS_URL

# 브로커가 없어도 import 는 되어야 한다 — 테스트가 이 모듈을 읽고 스케줄을 검사한다.
# 연결은 워커가 뜰 때 처음 시도된다.
app = Celery("daengs", broker=REDIS_URL or "memory://",
             include=["daengs_life.tasks.realtime", "daengs_life.tasks.crawl"])

app.conf.update(
    timezone="Asia/Seoul",          # 발표 주기가 전부 KST 다 (④-b)
    enable_utc=False,
    task_acks_late=True,
    worker_max_tasks_per_child=200,
    # 결과를 저장하지 않는다. 프리페치는 **캐시를 채우는 것이 결과**이고, 결과 백엔드를
    # 켜면 Redis 에 아무도 안 읽는 키가 쌓인다
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
)

# **스케줄이 하나다.** ④-b 의 발표 주기 7종을 여기 Beat 스케줄로 옮겨 적지 않는다 —
# 그러면 같은 7개 숫자가 `cache.yaml` 과 여기 두 곳에 살고, 한쪽만 고치는 날 조용히 어긋난다.
# 1분마다 깨어나 **무엇이 갱신 대상인지는 `cache.yaml` 이 정하게** 한다: `Cache.get` 이
# 발표를 하나도 안 놓친 키는 호출 없이 히트로 끝내므로, 잦은 틱은 비용이 아니라 해상도다.
# 1분인 이유는 AWS 매분자료가 가장 짧은 주기여서다 — 그보다 성기면 그 값이 항상 늦는다.
app.conf.beat_schedule = {
    "warm-active-grids": {
        "task": "daengs_life.tasks.realtime.warm_active",
        "schedule": 60.0,
    },
    # 크롤 쪽도 **등록은 하나뿐**이다 (RAG-001 원칙 4). 소스 30개의 주기를 여기 30줄로 옮겨
    # 적지 않는다 — 무엇이 밀렸는지는 `crawler.core.cadence` 가 시드와 `crawl_log.jsonl` 을
    # 보고 정한다. 위의 프리페치와 같은 이유로 스케줄과 판정을 갈라 둔다.
    #
    # 하루 한 번, KST 04:00. 새벽인 이유는 상대 서버가 한가한 시간이어서고(요청 간격 1.5초를
    # 지켜도 소스 12개면 수십 분이다), 04시인 이유는 자정 직후를 피해서다 — 공공 사이트의
    # 정기 점검이 0~3시에 몰려 있어 그때 받으면 HTTP 503 이 정상 응답처럼 쌓인다.
    "crawl-due-sources": {
        "task": "daengs_life.tasks.crawl.crawl_due",
        "schedule": crontab(hour=4, minute=0),
        # **큐를 나눈다.** 이 Beat 는 앱 전체 것이라 위의 프리페치도 같이 쏘는데, 둘이 같은
        # 큐에 있으면 크롤 워커(`--concurrency 1`)가 둘 다 먹는다. 그러면 04:00 크롤이 도는
        # 10~15분(후보 282대상 × 요청 간격 1.5초) 동안 프리페치가 줄을 서고, 끝나는 순간
        # 10여 개가 몰아서 실행되며 **팀 공용 data.go.kr 일 예산**을 쓴다 (D-019).
        #
        # 크롤 워커는 `--queues crawl` 로 이 큐만 먹는다. `warm_active` 는 기본 `celery` 큐에
        # 그대로 두고, 그 소비자는 RT-002 의 워커가 뜰 때 생긴다.
        "options": {"queue": "crawl"},
    },
}

__all__ = ["app"]
