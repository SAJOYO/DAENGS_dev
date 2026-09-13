"""Shared adapter execution for assistant routes and diary writing graphs.

Owns bounded concurrency and invocation outcomes, never publication or domain payloads.
Deadlines include queue time. Late provider values have no adoption authority.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

_late_tasks: set[asyncio.Task] = set()


def discard(task: asyncio.Task) -> None:
    def consume(done):
        _late_tasks.discard(done)
        if not done.cancelled():
            done.exception()

    if task.done():
        consume(task)
    else:
        _late_tasks.add(task)
        task.add_done_callback(consume)
        task.cancel()


@dataclass(frozen=True)
class ExecutionResult[T]:
    job_id: str
    status: Literal["ok", "timeout", "error"]
    value: T | None = None
    error_kind: str | None = None
    elapsed_ms: int = 0


class JobExecutor:
    def __init__(self, concurrency: int = 1):
        if concurrency < 1:
            raise ValueError("concurrency must be positive")
        self._semaphore = asyncio.Semaphore(concurrency)

    async def run[T](
        self,
        job_id: str,
        invoke: Callable[[], Awaitable[T]],
        *,
        deadline: float | None = None,
        timeout_ms: int | None = None,
    ) -> ExecutionResult[T]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        if timeout_ms is not None:
            cutoff = started + timeout_ms / 1000
            deadline = min(deadline, cutoff) if deadline is not None else cutoff

        async def before_cutoff(pending):
            if deadline is None:
                return await asyncio.shield(pending)
            done, _ = await asyncio.wait({pending}, timeout=max(0, deadline - loop.time()))
            if not done or loop.time() >= deadline:
                raise TimeoutError
            return pending.result()

        async def call():
            async with self._semaphore:
                if deadline is not None and loop.time() >= deadline:
                    raise TimeoutError
                # The lease belongs to this invocation's adoption window. A provider
                # ignoring cancellation cannot retain a slot and block later jobs.
                provider = asyncio.create_task(invoke())
                try:
                    return await before_cutoff(provider)
                finally:
                    discard(provider)

        task = None
        try:
            if deadline is not None and started >= deadline:
                raise TimeoutError
            task = asyncio.create_task(call(), name=job_id)
            value = await before_cutoff(task)
            return ExecutionResult(
                job_id, "ok", value, elapsed_ms=int((loop.time() - started) * 1000)
            )
        except TimeoutError:
            return ExecutionResult(
                job_id, "timeout", elapsed_ms=int((loop.time() - started) * 1000)
            )
        except Exception as exc:  # noqa: BLE001 - adapter failures are isolated; cancellation propagates
            return ExecutionResult(
                job_id,
                "error",
                error_kind=type(exc).__name__,
                elapsed_ms=int((loop.time() - started) * 1000),
            )
        finally:
            if task is not None:
                discard(task)
