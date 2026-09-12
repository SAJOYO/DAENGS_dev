"""점령지 사진 판정 전용 Celery 앱.

backend 웹은 ``territory-vision`` 큐에 발행만 하고, 이 별도 프로세스가 저장소와
Gemini를 호출합니다. 앱은 DB 상태를 polling하므로 Celery result backend는 없습니다.
"""

from __future__ import annotations

import asyncio
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
app.conf.broker_connection_timeout = 5
app.conf.broker_transport_options = {"socket_timeout": 5, "socket_connect_timeout": 5}
app.conf.task_publish_retry = False


@app.task(name="territory.verify_photo", bind=True, max_retries=MAX_RETRIES)
def verify_photo(self, attempt_id: str) -> None:
    """Fast retry delivery; model budget and failure completion belong to the DB lease."""
    from daengs_backend.services import territory_vision

    try:
        territory_vision.process_attempt_sync(attempt_id)
    except territory_vision.TerritoryVisionTransientError as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=2) from exc
        # The persisted retry/expired lease is still discoverable by recovery.


@app.task(name="territory.recover_photos")
def recover_photos():
    from daengs_backend.services.territory_vision_jobs import recover_pending

    return asyncio.run(recover_pending())


__all__ = ["MAX_RETRIES", "QUEUE_NAME", "app", "recover_photos", "verify_photo"]
