"""Real Redis + PostgreSQL + separate Celery solo processes, without Docker or a VLM key."""

import asyncio
import inspect
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from celery import Celery
from celery.beat import Scheduler
from redis import Redis
from sqlalchemy import func, select, text

from daengs_backend.config import settings
from daengs_backend.core.storage import StoredObject
from daengs_backend.models.territory import TerritoryAttempt, VerifiedVisit
from daengs_backend.services import territory


async def eventually(check, *, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return result
        await asyncio.sleep(0.05)
    pytest.fail("isolated runtime did not reach the expected state")


@pytest.fixture
async def runtime(database, actors, monkeypatch, tmp_path):
    broker = os.environ.get("TERRITORY_RUNTIME_TEST_REDIS")
    if not broker:
        pytest.skip("TERRITORY_RUNTIME_TEST_REDIS: disposable Redis not configured")
    target = urlparse(broker)
    assert target.hostname in {"127.0.0.1", "localhost"} and target.port not in {None, 6379}
    monkeypatch.setattr(settings, "activity_game_enabled", False)
    prefix = "territory_runtime_" + uuid.uuid4().hex + ":"
    redis = Redis.from_url(broker, decode_responses=True)
    app = Celery("territory-runtime", broker=broker, backend=broker, set_as_current=False)
    app.conf.update(
        task_default_queue="territory-vision",
        task_publish_retry=False,
        broker_transport_options={"global_keyprefix": prefix},
        result_backend_transport_options={"global_keyprefix": prefix},
    )
    async with database() as db:
        schema = await db.scalar(text("SELECT current_schema()"))
        attempt_id = uuid.uuid4()
        db.add(
            TerritoryAttempt(
                id=attempt_id,
                app_user_id=actors[0][0],
                client_capture_id=uuid.uuid4(),
                client_session_id=uuid.uuid4(),
                site_id="runtime-site",
                captured_at=datetime.now(UTC),
                capture_lat=37.5,
                capture_lng=127,
                site_lat=37.5,
                site_lng=127,
                accuracy_m=1,
                distance_m=0,
                is_mock=False,
                status="VISION_PENDING",
                photo_storage_key=str(attempt_id),
                photo_content_type="image/jpeg",
                photo_object_generation="runtime-generation",
                photo_size_bytes=36,
            )
        )
        await db.commit()
    env = {
        **os.environ,
        "REDIS_URL": broker,
        "GEMINI_API_KEY": "",
        "TERRITORY_RUNTIME_TEST_SCHEMA": schema,
        "TERRITORY_RUNTIME_TEST_PREFIX": prefix,
        "DAENGS_ACTIVITY_GAME_ENABLED": "false",
    }
    workers, logs = [], []

    async def start():
        name = f"{schema}-{len(workers)}@localhost"
        log = (tmp_path / f"worker-{len(workers)}.log").open("w")
        logs.append(log)
        worker = await asyncio.to_thread(
            subprocess.Popen,
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "tests.territory.support.vision_worker:app",
                "worker",
                "--pool=solo",
                "--concurrency=1",
                "--queues=territory-vision",
                f"--hostname={name}",
                "--loglevel=INFO",
                "--without-gossip",
                "--without-mingle",
            ],
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        workers.append(worker)

        async def ready():
            assert worker.poll() is None, f"worker exited; inspect {log.name}"
            return await asyncio.to_thread(app.control.ping, destination=[name], timeout=1)

        await eventually(ready, timeout=30)
        return worker

    async def saved():
        async with database() as db:
            return await db.get(TerritoryAttempt, attempt_id)

    async def completed():
        row = await saved()
        return row if row.status == "VERIFIED" and row.photo_redacted_at else None

    def tick():
        from daengs_life.tasks.celery_app import app as crawler

        # Use the production entry and the real Beat scheduler without unrelated crawl jobs.
        app.conf.beat_schedule = {
            "recover-territory-photos": crawler.conf.beat_schedule["recover-territory-photos"]
        }
        scheduler = Scheduler(app=app)
        try:
            entry = scheduler.schedule["recover-territory-photos"]
            entry.last_run_at = app.now() - timedelta(seconds=31)
            scheduler.tick()
        finally:
            scheduler.close()

    def recovery_events():
        records = []
        marker = '{"event": "territory_vision_recovery"'
        for log in logs:
            for line in Path(log.name).read_text(encoding="utf-8", errors="replace").splitlines():
                offset = line.find(marker)
                if offset >= 0:
                    records.append(json.loads(line[offset:]))
        return records

    try:
        yield SimpleNamespace(
            app=app,
            redis=redis,
            prefix=prefix,
            attempt_id=attempt_id,
            start=start,
            saved=saved,
            completed=completed,
            tick=tick,
            recovery_events=recovery_events,
        )
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            await asyncio.to_thread(worker.wait, timeout=15)
        for log in logs:
            log.close()
        keys = list(redis.scan_iter(match=prefix + "*"))
        if keys:
            redis.delete(*keys)
        redis.close()
        app.close()


async def test_two_workers_ack_duplicate_without_a_second_model_call(runtime, database):
    rt = runtime
    await rt.start()
    await rt.start()
    key = str(rt.attempt_id)
    rt.redis.set(rt.prefix + "block:" + key, "1")
    first = rt.app.send_task("territory.verify_photo", args=[key])
    await eventually(lambda: rt.redis.get(rt.prefix + "calls:" + key) == "1")
    duplicate = rt.app.send_task("territory.verify_photo", args=[key])
    await eventually(duplicate.ready)
    assert duplicate.successful()
    assert rt.redis.get(rt.prefix + "calls:" + key) == "1"
    rt.redis.delete(rt.prefix + "block:" + key)
    row = await eventually(rt.completed)
    await eventually(first.ready)
    assert first.successful() and row.vision_attempts == 1
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(VerifiedVisit)) == 1


async def test_broker_connection_failure_is_recovered_by_beat_without_reconfirm(
    runtime, database, actors, monkeypatch
):
    from daengs_backend.tasks.territory import verify_photo

    rt = runtime
    async with database() as db:
        row = await db.get(TerritoryAttempt, rt.attempt_id)
        row.status = "PENDING_UPLOAD"
        row.photo_object_generation = row.photo_size_bytes = None
        await db.commit()
    storage = SimpleNamespace(stat=lambda _: StoredObject("runtime-generation", 36, "image/jpeg"))
    monkeypatch.setattr(territory, "get_storage", lambda: storage)
    # A bound, non-listening socket guarantees an isolated real connection refusal.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        broken = Celery(
            "unavailable",
            broker=f"redis://127.0.0.1:{unavailable.getsockname()[1]}/0",
            set_as_current=False,
        )
        broken.conf.update(task_publish_retry=False, broker_connection_timeout=1)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(
                    verify_photo,
                    "delay",
                    lambda key: broken.send_task("territory.verify_photo", args=[key], retry=False),
                )
                async with database() as db:
                    with pytest.raises(territory.TerritoryVisionQueueUnavailable):
                        await territory.confirm_upload(db, actors[0][0], rt.attempt_id)
        finally:
            broken.close()
    assert (await rt.saved()).status == "VISION_PENDING"
    await rt.start()
    await asyncio.to_thread(rt.tick)
    row = await eventually(rt.completed)
    assert row.vision_attempts == 1
    assert rt.redis.get(rt.prefix + "calls:" + str(rt.attempt_id)) == "1"
    events = await eventually(
        lambda: rt.recovery_events() if len(rt.recovery_events()) >= 2 else None
    )
    assert events[-1]["phase"] == "finished" and events[-1]["published"] == 1
    assert events[-1]["failed"] == events[-1]["deferred"] == 0


async def test_killed_worker_is_recovered_after_lease_expiry(runtime, database):
    rt = runtime
    worker = await rt.start()
    key = str(rt.attempt_id)
    rt.redis.set(rt.prefix + "block:" + key, "1")
    rt.app.send_task("territory.verify_photo", args=[key])
    await eventually(lambda: rt.redis.get(rt.prefix + "calls:" + key) == "1")
    leased = await rt.saved()
    assert leased.vision_lease_token is not None and leased.vision_attempts == 1
    worker.kill()
    await asyncio.to_thread(worker.wait, timeout=15)
    assert (await rt.saved()).vision_lease_token == leased.vision_lease_token
    rt.redis.delete(rt.prefix + "block:" + key)
    async with database() as db:
        row = await db.get(TerritoryAttempt, rt.attempt_id)
        # Advance the persisted deadline, without sleeping through the production 66-second lease.
        row.vision_lease_until = row.vision_available_at = row.vision_dispatch_after = datetime.now(
            UTC
        ) - timedelta(seconds=1)
        await db.commit()
    await rt.start()
    await asyncio.to_thread(rt.tick)
    row = await eventually(rt.completed)
    assert row.vision_attempts == 2 and row.vision_lease_token is None
    assert rt.redis.get(rt.prefix + "calls:" + key) == "2"


async def test_empty_recovery_logs_are_visible_in_actual_celery_worker(runtime, database):
    rt = runtime
    async with database() as db:
        row = await db.get(TerritoryAttempt, rt.attempt_id)
        row.vision_available_at = datetime.now(UTC) + timedelta(days=1)
        await db.commit()
    await rt.start()
    result = rt.app.send_task("territory.recover_photos")
    await eventually(result.ready)
    assert result.successful()
    assert result.get(timeout=1) == {
        "selected": 0,
        "attempted": 0,
        "published": 0,
        "failed": 0,
        "deferred": 0,
    }
    events = await eventually(
        lambda: rt.recovery_events() if len(rt.recovery_events()) >= 2 else None
    )
    assert [event["phase"] for event in events] == ["started", "finished"]
    assert events[0]["run_id"] == events[1]["run_id"]
    assert events[1]["unconfirmed"] == 0
    assert events[1]["elapsed_ms"] >= 0
    assert str(rt.attempt_id) not in json.dumps(events)
    assert rt.redis.get(rt.prefix + "calls:" + str(rt.attempt_id)) is None
