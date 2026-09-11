"""Two real Redis leases; the endpoint must be an explicitly disposable loopback DB."""

import os
import subprocess
import sys
import time
from datetime import timedelta
from urllib.parse import urlparse

import pytest
from celery import Celery

from daengs_backend.tasks.activity_scheduler import LOCK_KEY, ActivityScheduler


def test_due_beats_publish_only_one_activity_message(monkeypatch, tmp_path):
    from daengs_backend.config import settings
    from daengs_backend.tasks import activity, activity_scheduler

    url = os.environ.get("ACTIVITY_RUNTIME_TEST_REDIS")
    if not url:
        pytest.skip("ACTIVITY_RUNTIME_TEST_REDIS: disposable Redis not configured")
    target = urlparse(url)
    assert target.hostname in {"127.0.0.1", "localhost"} and target.port not in {None, 6379}
    monkeypatch.setattr(settings, "activity_game_enabled", True)
    monkeypatch.setattr(activity_scheduler, "HEARTBEAT", tmp_path / "beat.json")
    instances = []
    for name in ("publisher-one", "publisher-two"):
        app = Celery(name, broker=url)
        app.conf.update(beat_schedule=activity.app.conf.beat_schedule, result_expires=None)
        scheduler = ActivityScheduler(app=app)
        assert set(scheduler.schedule) == {"activity-process"}
        scheduler.schedule["activity-process"].last_run_at = app.now() - timedelta(seconds=31)
        instances.append(scheduler)
    first, second = instances
    try:
        assert first.redis.llen("activity") == 0
        first.tick()
        second.tick()
        assert first.redis.llen("activity") == 1
    finally:
        first.redis.delete("activity")
        for instance in instances:
            instance.close()
            instance.app.close()


def test_only_one_publisher_and_expired_owner_cannot_release_successor():
    url = os.environ.get("ACTIVITY_RUNTIME_TEST_REDIS")
    if not url:
        pytest.skip("ACTIVITY_RUNTIME_TEST_REDIS: disposable Redis not configured")
    target = urlparse(url)
    assert target.hostname in {"127.0.0.1", "localhost"} and target.port not in {None, 6379}
    first = ActivityScheduler(app=Celery("first", broker=url))
    second = ActivityScheduler(app=Celery("second", broker=url))
    try:
        assert first.redis.get(LOCK_KEY) is None, "test Redis must be empty"
        assert first._leader()
        assert not second._leader()
        assert first._leader()  # renewal does not release ownership
        assert 0 < first.redis.ttl(LOCK_KEY) <= 60
        first.redis.delete(LOCK_KEY)  # simulate expired/crashed owner
        assert second._leader()
        assert not first._leader()
        first.close()
        assert second._leader()  # stale owner cannot delete successor's token
    finally:
        second.close()
        first.close()


def test_disabled_task_travels_through_real_broker_without_database(tmp_path):
    url = os.environ.get("ACTIVITY_RUNTIME_TEST_REDIS")
    if not url:
        pytest.skip("ACTIVITY_RUNTIME_TEST_REDIS: disposable Redis not configured")
    target = urlparse(url)
    assert target.hostname in {"127.0.0.1", "localhost"} and target.port not in {None, 6379}
    app = Celery("transport-check", broker=url, backend=url)
    env = {
        **os.environ,
        "REDIS_URL": url,
        "DAENGS_ACTIVITY_GAME_ENABLED": "false",
        "DAENGS_DB_HOST": "invalid.invalid",
    }
    with (tmp_path / "worker.log").open("w") as log:
        worker = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "daengs_backend.tasks.activity:app",
                "--broker",
                url,
                "--result-backend",
                url,
                "worker",
                "--pool=solo",
                "--concurrency=1",
                "--queues=activity",
                "--hostname=runtime-test@localhost",
                "--loglevel=WARNING",
                "--without-gossip",
                "--without-mingle",
            ],
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                assert worker.poll() is None, "isolated worker exited during startup"
                if app.control.ping(destination=["runtime-test@localhost"], timeout=1):
                    break
            else:
                pytest.fail("isolated worker did not answer ping")
            result = app.send_task("activity.process", queue="activity", expires=30)
            assert result.get(timeout=15) == {"status": "disabled", "processed": 0}
            result.forget()
        finally:
            worker.terminate()
            worker.wait(timeout=15)
            app.close()
