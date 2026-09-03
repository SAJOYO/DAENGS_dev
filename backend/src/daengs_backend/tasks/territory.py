"""점령지 사진 판정 전용 Celery 앱.

backend 웹은 ``territory-vision`` 큐에 발행만 하고, 이 별도 프로세스가 저장소와
Gemini를 호출합니다. 앱은 DB 상태를 polling하므로 Celery result backend는 없습니다.
"""

from __future__ import annotations

import os

from celery import Celery

QUEUE_NAME = "territory-vision"
MAX_RETRIES = 1

app = Celery(
    "daengs_backend.territory_vision",
    broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    backend=None,
)
app.conf.task_default_queue = QUEUE_NAME
app.conf.task_acks_late = True
app.conf.task_reject_on_worker_lost = True
app.conf.worker_prefetch_multiplier = 1


@app.task(name="territory.verify_photo", bind=True, max_retries=MAX_RETRIES)
def verify_photo(self, attempt_id: str) -> None:
    """사진 판정. 기술 실패만 2초 뒤 한 번 재시도하고 반드시 DB 상태로 끝냅니다."""
    from daengs_backend.services import territory_vision

    try:
        territory_vision.process_attempt_sync(attempt_id)
    except territory_vision.TerritoryVisionTransientError as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=2) from exc
        territory_vision.record_failed_attempt_sync(attempt_id, reason=exc.reason_code)
    except territory_vision.TerritoryVisionPermanentError as exc:
        territory_vision.record_failed_attempt_sync(attempt_id, reason=exc.reason_code)


__all__ = ["MAX_RETRIES", "QUEUE_NAME", "app", "verify_photo"]
