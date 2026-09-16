"""Shared executor behavior, including deadlines while queued and late providers."""

import asyncio

import pytest

from daengs_backend.orchestration.execution import JobExecutor


async def test_failure_does_not_cancel_a_sibling():
    executor = JobExecutor(2)

    async def bad():
        raise OSError("must not appear in the outcome")

    async def good():
        await asyncio.sleep(0)
        return "kept"

    failed, kept = await asyncio.gather(executor.run("bad", bad), executor.run("good", good))
    assert failed.status == "error" and failed.error_kind == "OSError"
    assert kept.status == "ok" and kept.value == "kept"
    assert "must not appear" not in repr(failed)


async def test_queue_wait_uses_original_deadline_and_never_starts_expired_call():
    executor = JobExecutor(1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def occupied():
        entered.set()
        await release.wait()

    first = asyncio.create_task(executor.run("first", occupied))
    await entered.wait()
    calls = []

    async def queued():
        calls.append("started")

    try:
        result = await executor.run("queued", queued, timeout_ms=15)
        assert result.status == "timeout" and calls == []
    finally:
        release.set()
        await first
    assert calls == []


async def test_cancellation_resistant_provider_cannot_extend_deadline_or_return_late_value():
    release, finished = asyncio.Event(), asyncio.Event()
    executor = JobExecutor()

    async def stubborn():
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        finished.set()
        return "too late"

    try:
        result = await asyncio.wait_for(executor.run("late", stubborn, timeout_ms=15), 1)
        assert result.status == "timeout" and result.value is None
        assert not finished.is_set()

        async def next_job():
            return "next"

        next_result = await asyncio.wait_for(executor.run("next", next_job), 1)
        assert next_result.value == "next" and not finished.is_set()
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), 1)
    assert result.value is None


async def test_parent_cancellation_is_not_reported_as_provider_failure():
    started = asyncio.Event()

    async def waiting():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(JobExecutor().run("cancel", waiting))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
