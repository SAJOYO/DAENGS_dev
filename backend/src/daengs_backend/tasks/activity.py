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
    beat_schedule={"activity-process": {"task": "activity.process", "schedule": 30.0}},
)


async def _process():
    from daengs_backend.core.database import worker_session
    from daengs_backend.services.activity import process_pending

    async with worker_session() as db:
        return await process_pending(db)


@app.task(name="activity.process")
def process():
    return asyncio.run(_process())
