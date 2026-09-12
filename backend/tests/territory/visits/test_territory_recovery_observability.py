"""Recovery event meaning, failures and cancellation; no external broker or database."""

import asyncio
import json
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from daengs_backend.services import territory_vision_jobs as jobs
from daengs_backend.services.territory import TerritoryVisionQueueUnavailable


@pytest.fixture
def recovery(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=jobs.__name__)
    session = SimpleNamespace(commit=AsyncMock())
    rows = [SimpleNamespace(id=uuid.uuid4(), vision_dispatch_after=None) for _ in range(100)]
    monkeypatch.setattr(jobs.repo, "due_dispatches", AsyncMock(return_value=rows))
    sessions = []

    @asynccontextmanager
    async def factory():
        sessions.append(session)
        try:
            yield session
        finally:
            sessions.remove(session)

    def events():
        records = [
            json.loads(record.message) for record in caplog.records if record.name == jobs.__name__
        ]
        assert len(records) == 2
        start, end = records
        assert start["phase"] == "started"
        assert start["run_id"] == end["run_id"]
        assert uuid.UUID(start["run_id"])
        assert start["event"] == end["event"] == "territory_vision_recovery"
        assert start["version"] == end["version"] == 1
        assert datetime.fromisoformat(end["at"]) >= datetime.fromisoformat(start["at"])
        assert end["elapsed_ms"] >= start["elapsed_ms"] >= 0
        assert end["attempted"] == end["published"] + end["failed"] + end["unconfirmed"]
        assert end["selected"] == end["attempted"] + end["deferred"]
        for row in rows:
            assert str(row.id) not in caplog.text
        return end

    return SimpleNamespace(
        factory=factory, session=session, rows=rows, sessions=sessions, events=events
    )


@pytest.mark.parametrize("size", [0, 3])
async def test_empty_and_successful_runs_emit_start_finish(recovery, size):
    del recovery.rows[size:]
    published = []

    def publish(attempt_id):
        assert recovery.sessions == []
        recovery.session.commit.assert_awaited_once()
        published.append(attempt_id)

    result = await jobs.recover_pending(factory=recovery.factory, publish=publish)
    assert result == {
        "selected": size,
        "attempted": size,
        "published": size,
        "failed": 0,
        "deferred": 0,
    }
    assert published == [row.id for row in recovery.rows]
    assert all(row.vision_dispatch_after is not None for row in recovery.rows)
    end = recovery.events()
    assert end["phase"] == "finished" and "error_type" not in end


@pytest.mark.parametrize("successes", [0, 3, 99])
async def test_broker_failure_counts_only_attempted_failure_and_keeps_deferred(
    recovery, caplog, successes
):
    calls = []

    def publish(attempt_id):
        calls.append(attempt_id)
        if len(calls) > successes:
            raise TerritoryVisionQueueUnavailable("private broker password and photo key")

    result = await jobs.recover_pending(factory=recovery.factory, publish=publish)
    assert result == {
        "selected": 100,
        "attempted": successes + 1,
        "published": successes,
        "failed": 1,
        "deferred": 99 - successes,
    }
    assert len(calls) == successes + 1
    end = recovery.events()
    assert end["phase"] == "finished"
    assert end["error_type"] == "TerritoryVisionQueueUnavailable"
    assert "private broker password" not in caplog.text


async def test_reservation_failure_does_not_claim_committed_selection(recovery, caplog):
    failure = RuntimeError("private database connection")
    recovery.session.commit.side_effect = failure
    with pytest.raises(RuntimeError) as raised:
        await jobs.recover_pending(
            factory=recovery.factory, publish=lambda _: pytest.fail("not reserved")
        )
    assert raised.value is failure
    end = recovery.events()
    assert end["phase"] == "failed" and end["stage"] == "reserve"
    assert end["selected"] == end["attempted"] == 0
    assert end["error_type"] == "RuntimeError"
    assert "private database" not in caplog.text


async def test_unexpected_publish_error_is_logged_and_propagated(recovery, caplog):
    failure = ValueError("private provider response")

    def publish(_):
        raise failure

    with pytest.raises(ValueError) as raised:
        await jobs.recover_pending(factory=recovery.factory, publish=publish)
    assert raised.value is failure
    end = recovery.events()
    assert end["phase"] == "failed" and end["stage"] == "publish"
    assert end["published"] == 0 and end["failed"] == 1 and end["deferred"] == 99
    assert "private provider" not in caplog.text


async def test_cancelled_thread_publication_is_unconfirmed_not_failed_or_successful(recovery):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def publish(_):
        started.set()
        try:
            assert release.wait(5)
        finally:
            finished.set()

    task = asyncio.create_task(jobs.recover_pending(factory=recovery.factory, publish=publish))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        end = recovery.events()
        assert end["phase"] == "cancelled"
        assert end["unconfirmed"] == 1 and end["deferred"] == 99
        assert end["published"] == end["failed"] == 0
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 3)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_cancelled_reservation_is_reported_without_dispatch(recovery):
    recovery.session.commit.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await jobs.recover_pending(
            factory=recovery.factory, publish=lambda _: pytest.fail("not reserved")
        )
    end = recovery.events()
    assert end["phase"] == "cancelled" and end["stage"] == "reserve"
    assert end["selected"] == end["unconfirmed"] == 0
