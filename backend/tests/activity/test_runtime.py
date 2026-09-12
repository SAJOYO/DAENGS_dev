"""Runtime gates; no shared DB/Redis, no real photo or point requests."""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from celery import Celery
from celery.beat import Scheduler
from redis.exceptions import ConnectionError

from daengs_backend.cli.activity_runtime import VERIFIERS, beat_healthy, check_schema
from daengs_backend.config import settings
from daengs_backend.tasks import activity, activity_scheduler


async def test_disabled_message_never_opens_database(monkeypatch):
    from daengs_backend.core import database

    monkeypatch.setattr(settings, "activity_game_enabled", False)
    session = Mock(side_effect=AssertionError("DB accessed while disabled"))
    monkeypatch.setattr(database, "worker_session", session)
    assert await activity._process() == {"status": "disabled", "processed": 0}
    session.assert_not_called()


async def test_enabled_message_runs_existing_processor_and_closes_session(monkeypatch):
    from daengs_backend.core import database
    from daengs_backend.services import activity as service

    monkeypatch.setattr(settings, "activity_game_enabled", True)
    events = []
    db = object()

    @asynccontextmanager
    async def session():
        events.append("open")
        try:
            yield db
        finally:
            events.append("close")

    run = AsyncMock(return_value=3)
    monkeypatch.setattr(database, "worker_session", session)
    monkeypatch.setattr(service, "process_pending", run)
    assert await activity._process() == {"status": "processed", "processed": 3}
    run.assert_awaited_once_with(db)
    assert events == ["open", "close"]


@pytest.fixture
def scheduler(monkeypatch, tmp_path):
    client = Mock()
    client.lock.return_value.owned.return_value = False
    client.lock.return_value.acquire.return_value = True
    monkeypatch.setattr(activity_scheduler.Redis, "from_url", Mock(return_value=client))
    monkeypatch.setattr(activity_scheduler, "HEARTBEAT", tmp_path / "heartbeat.json")
    monkeypatch.setattr(settings, "activity_game_enabled", True)
    app = Celery("runtime-test", broker="redis://127.0.0.1:1/0")
    instance = activity_scheduler.ActivityScheduler(app=app)
    yield instance
    instance.close()


def test_disabled_beat_has_heartbeat_without_broker_or_dispatch(scheduler, monkeypatch):
    monkeypatch.setattr(settings, "activity_game_enabled", False)
    parent = Mock()
    monkeypatch.setattr(Scheduler, "tick", parent)
    assert scheduler.tick() == 5
    scheduler.lease.owned.assert_not_called()
    parent.assert_not_called()
    assert json.loads(activity_scheduler.HEARTBEAT.read_text())["state"] == "disabled"


def test_standby_never_dispatches(scheduler, monkeypatch):
    scheduler.lease.acquire.return_value = False
    parent = Mock()
    monkeypatch.setattr(Scheduler, "tick", parent)
    assert scheduler.tick() == 5
    parent.assert_not_called()
    assert json.loads(activity_scheduler.HEARTBEAT.read_text())["state"] == "standby"


def test_lease_renewal_and_tick_bound(scheduler, monkeypatch):
    scheduler.lease.owned.return_value = True
    parent = Mock(return_value=30)
    monkeypatch.setattr(Scheduler, "tick", parent)
    assert scheduler.tick() == 5
    scheduler.lease.reacquire.assert_called_once()
    parent.assert_called_once()


def test_redis_outage_stops_publishing(scheduler, monkeypatch):
    scheduler.lease.owned.side_effect = ConnectionError("test outage")
    send = Mock()
    monkeypatch.setattr(Scheduler, "apply_entry", send)
    scheduler.apply_entry(object())
    send.assert_not_called()


def test_lease_loss_before_publish_skips_entry(scheduler, monkeypatch):
    assert scheduler._leader()
    scheduler.lease.acquire.return_value = False
    send = Mock()
    monkeypatch.setattr(Scheduler, "apply_entry", send)
    scheduler.apply_entry(object())
    send.assert_not_called()


@pytest.mark.parametrize(
    "state,age,expected",
    [
        ("leader", 5, True),
        ("disabled", 5, True),
        ("standby", 5, False),
        ("leader", 31, False),
        ("leader", -1, False),
    ],
)
def test_heartbeat_health(tmp_path, state, age, expected):
    path = tmp_path / "heartbeat.json"
    path.write_text(json.dumps({"state": state, "at": 100 - age}))
    assert beat_healthy(path, 100) == expected


async def test_preflight_uses_readonly_transaction_and_does_not_create_season(
    monkeypatch, tmp_path
):
    import asyncpg

    conn = AsyncMock()
    options = []

    @asynccontextmanager
    async def transaction(**kwargs):
        options.append(kwargs)
        yield

    conn.transaction = transaction
    conn.fetchval.return_value = 0
    connect = AsyncMock(return_value=conn)
    monkeypatch.setattr(asyncpg, "connect", connect)
    for name in VERIFIERS:
        (tmp_path / f"verify_{name}.sql").write_text("SELECT 1")
    result = await check_schema(tmp_path)
    assert options == [{"isolation": "repeatable_read", "readonly": True}]
    assert result["schema_checks"] == 8 and result["active_seasons"] == 0
    assert isinstance(connect.call_args.kwargs["password"], str)
    conn.close.assert_awaited_once()
    conn.commit.assert_not_called()


async def test_preflight_failure_closes_connection(monkeypatch, tmp_path):
    import asyncpg

    conn = AsyncMock()
    conn.transaction = Mock(return_value=AsyncMock())
    monkeypatch.setattr(asyncpg, "connect", AsyncMock(return_value=conn))
    with pytest.raises(FileNotFoundError):
        await check_schema(tmp_path)
    conn.close.assert_awaited_once()
