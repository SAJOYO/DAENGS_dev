"""Dedicated activity projection queue; run Beat only after explicit activation."""

import asyncio
import os

from celery import Celery

app = Celery(
    "daengs_backend.activity", broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0")
)
app.conf.update(
    task_default_queue="activity",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    beat_scheduler="daengs_backend.tasks.activity_scheduler:ActivityScheduler",
    beat_max_loop_interval=5,
    broker_connection_timeout=5,
    broker_transport_options={"socket_timeout": 5, "socket_connect_timeout": 5},
    task_publish_retry=False,
    result_expires=None,
    beat_schedule={
        "activity-process": {
            "task": "activity.process",
            "schedule": 30.0,
            "options": {"queue": "activity", "expires": 60},
        }
    },
)


async def _process():
    from daengs_backend.config import settings

    # Old queued messages may still arrive after an operator disables the game.
    # Do not open a DB session or turn this expected state into repeated failures.
    if not settings.activity_game_enabled:
        return {"status": "disabled", "processed": 0}

    from daengs_backend.core.database import worker_session
    from daengs_backend.services.activity import process_pending

    async with worker_session() as db:
        return {"status": "processed", "processed": await process_pending(db)}


@app.task(name="activity.process")
def process():
    return asyncio.run(_process())
