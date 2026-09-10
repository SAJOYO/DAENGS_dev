"""Opt-in worker/Beat for durable record context jobs. Never shares the crawler queue."""

import asyncio
import logging
import os

from celery import Celery

app = Celery(
    "daengs_backend.walk_entry_context",
    broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
)
app.conf.update(
    task_default_queue="walk-entry-context",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "record-context-process": {"task": "walk_entry_context.process", "schedule": 30.0},
        "public-catalog-refresh": {
            "task": "walk_public_catalog.refresh",
            "schedule": 60.0,
            "options": {"queue": "walk-public-catalog", "expires": 55},
        },
    },
)


@app.task(name="walk_entry_context.process")
def process():
    from daengs_backend.core.database import worker_session
    from daengs_backend.services.walk_entry_context import process as run

    return asyncio.run(run(worker_session))


@app.task(name="walk_public_catalog.refresh", ignore_result=True)
def refresh_catalogs():
    from daengs_backend.core.database import worker_session
    from daengs_backend.services.walk_catalog_refresh import run

    result = asyncio.run(run(worker_session))
    if result["status"] in {"unavailable", "not_configured"} or result.get("failed"):
        # Result contains only counts/status and exception class, never raw source errors.
        logging.getLogger(__name__).warning("walk_catalog_refresh %s", result)
    return result
